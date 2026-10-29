import * as THREE from 'three';
import { OrbitControls } from './vendor/three/OrbitControls.js';

// Positions are stable schematic slots, never coordinates of the actual venue.
const PALETTE = {
  breached: '#ff754b', hot: '#d9af70', secure: '#b5f18c',
  unknown: '#9a92b5', stale: '#718288',
};
const STATE_LABEL = {
  breached: 'BREACH', hot: 'ANOMALY', secure: 'HOLDING',
  unknown: 'UNVERIFIED', stale: 'NOT UPDATED',
};
const SLOT_X = 5.35;
const SLOT_Z = 4.55;

function positionForSlot(slot) {
  return new THREE.Vector3((slot % 3 - 1) * SLOT_X, 0, Math.floor(slot / 3) * SLOT_Z - SLOT_Z / 2);
}

function chamferedShape(width, depth, corner = 0.26) {
  const shape = new THREE.Shape();
  const x = width / 2;
  const y = depth / 2;
  shape.moveTo(-x + corner, -y);
  shape.lineTo(x - corner, -y);
  shape.lineTo(x, -y + corner);
  shape.lineTo(x, y - corner);
  shape.lineTo(x - corner, y);
  shape.lineTo(-x + corner, y);
  shape.lineTo(-x, y - corner);
  shape.lineTo(-x, -y + corner);
  shape.closePath();
  return shape;
}

export class SectorScene {
  constructor(container, { onSelect, onReady } = {}) {
    this.container = container;
    this.onSelect = onSelect;
    this.onReady = onReady;
    this.rooms = new Map();
    this.items = new Map();
    this.geometries = new Map();
    this.materials = new Map();
    this.selectedId = null;
    this.view = 'iso';
    this.disposed = false;
    this.motionRequested = true;
    this.mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    this.motion = !this.mediaQuery.matches;
    this._mediaChange = () => this.setMotion(this.motionRequested);
    this.mediaQuery.addEventListener?.('change', this._mediaChange);
    this.container.style.position = 'relative';
    this.container.classList.add('sector-scene');
    this._lastFrame = 0;
    this._time = 0;
    this._visibility = () => {
      this._lastFrame = 0;
      if (!document.hidden && !this.disposed) this._renderFrame(performance.now());
    };
    document.addEventListener('visibilitychange', this._visibility);

    try {
      this._createRenderer();
      this.webgl = true;
      this._buildScene();
      this._bindInteractions();
      this.resizeObserver = new ResizeObserver(() => this._resize());
      this.resizeObserver.observe(container);
      this._resize();
      this.overview();
      this._animate = (time) => {
        if (this.disposed || !this.webgl) return;
        this._raf = requestAnimationFrame(this._animate);
        if (document.hidden || time - this._lastFrame < 28) return;
        this._renderFrame(time);
      };
      this._raf = requestAnimationFrame(this._animate);
    } catch (error) {
      console.info('3D view unavailable; using the interactive schematic.', error.message);
      this._fallback();
    }
    queueMicrotask(() => { if (!this.disposed) this.onReady?.({ webgl: this.webgl }); });
  }

  _createRenderer() {
    this.renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.35;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFShadowMap;
    this.canvas = this.renderer.domElement;
    this.canvas.className = 'sector-canvas';
    this.canvas.setAttribute('aria-label', 'Interactive schematic sector board. Drag to rotate, scroll to zoom, or select a labeled sector.');
    this.canvas.setAttribute('role', 'img');
    Object.assign(this.canvas.style, { display: 'block', width: '100%', height: '100%', outline: 'none', touchAction: 'none' });
    this.container.append(this.canvas);
    this._contextLost = (event) => {
      event.preventDefault();
      if (!this.disposed) {
        this._fallback();
        this.onReady?.({ webgl: false });
      }
    };
    this.canvas.addEventListener('webglcontextlost', this._contextLost);
  }

  _material(name, color, extra = {}) {
    if (!this.materials.has(name)) {
      this.materials.set(name, new THREE.MeshStandardMaterial({ color, roughness: 0.75, metalness: 0.3, ...extra }));
    }
    return this.materials.get(name);
  }

  _geometry(name, create) {
    if (!this.geometries.has(name)) this.geometries.set(name, create());
    return this.geometries.get(name);
  }

  _box(parent, size, at, material, cast = false) {
    const mesh = new THREE.Mesh(this._geometry('box', () => new THREE.BoxGeometry(1, 1, 1)), material);
    mesh.scale.set(...size);
    mesh.position.set(...at);
    mesh.castShadow = cast;
    mesh.receiveShadow = true;
    parent.add(mesh);
    return mesh;
  }

  _slab(parent, width, depth, height, y, material, key) {
    const geometry = this._geometry(key, () => {
      const geo = new THREE.ExtrudeGeometry(chamferedShape(width, depth), { depth: height, bevelEnabled: false });
      geo.rotateX(-Math.PI / 2);
      return geo;
    });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.position.y = y;
    mesh.receiveShadow = true;
    parent.add(mesh);
    return mesh;
  }

  _buildScene() {
    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.Fog('#111a1d', 32, 90);
    this.camera = new THREE.PerspectiveCamera(35, 1, 0.1, 160);
    this.camera.position.set(17, 19, 23);
    this.controls = new OrbitControls(this.camera, this.canvas);
    this.controls.enableDamping = this.motion;
    this.controls.dampingFactor = 0.09;
    this.controls.minDistance = 6;
    this.controls.maxDistance = 65;
    this.controls.maxPolarAngle = Math.PI / 2.35;
    this.controls.minPolarAngle = 0.02;
    this.controls.enablePan = true;
    this.controls.screenSpacePanning = false;
    this.controls.rotateSpeed = 0.6;
    this.controls.zoomSpeed = 0.8;
    this.controls.target.set(0, 0, 0);
    this._controlStart = () => { this.transition = null; };
    this.controls.addEventListener('start', this._controlStart);

    this.scene.add(new THREE.HemisphereLight('#c4e9d6', '#1b152e', 2.3));
    const key = new THREE.DirectionalLight('#e2f9e4', 3.2);
    key.position.set(9, 18, 8);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    Object.assign(key.shadow.camera, { left: -19, right: 19, top: 19, bottom: -19, near: 1, far: 60 });
    key.shadow.bias = -0.0003;
    key.shadow.normalBias = 0.035;
    this.scene.add(key);
    const rim = new THREE.DirectionalLight('#a997e6', 1.7);
    rim.position.set(-12, 7, -10);
    this.scene.add(rim);

    this.platform = new THREE.Group();
    this.scene.add(this.platform);
    this.boardBody = this._box(this.platform, [16.5, 0.3, 10.6], [0, -0.52, 0], this._material('boardBody', '#182227', { metalness: 0.65 }));
    this.boardTop = this._box(this.platform, [16.25, 0.07, 10.35], [0, -0.33, 0], this._material('boardTop', '#202d30'));
    this.boardBase = this._box(this.platform, [16.1, 0.12, 10.25], [0, -0.75, 0], this._material('base', '#0c1115'));
    this.edge = this._material('boardEdge', '#526656', { emissive: '#7da76c', emissiveIntensity: 0.16 });
    this.boardFront = this._box(this.platform, [14.9, 0.025, 0.025], [0, -0.49, 5.31], this.edge);
    this.boardBack = this._box(this.platform, [14.9, 0.025, 0.025], [0, -0.49, -5.31], this.edge);
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(180, 180), this._material('ground', '#111a1d', { roughness: 1, metalness: 0 }));
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = -0.9;
    floor.receiveShadow = true;
    this.scene.add(floor);
    this.groundGeometry = floor.geometry;
    const grid = new THREE.GridHelper(120, 100, '#3e534c', '#293c36');
    grid.position.y = -0.885;
    grid.material.transparent = true;
    grid.material.opacity = 0.17;
    this.scene.add(grid);
    this.grid = grid;

    // A faint measuring grid reads as a tactical table, not implied corridors.
    this.boardGrid = new THREE.GridHelper(15.5, 31, '#53635d', '#45534f');
    this.boardGrid.position.y = -0.282;
    this.boardGrid.material.transparent = true;
    this.boardGrid.material.opacity = 0.18;
    this.boardGrid.scale.z = 10 / 15.5;
    this.platform.add(this.boardGrid);
    this.boardTicks = new THREE.Group();
    this.platform.add(this.boardTicks);
    const tickMat = this._material('tick', '#7a8a7f', { emissive: '#7a8a7f', emissiveIntensity: 0.1 });
    for (let i = -15; i <= 15; i += 1) {
      this._box(this.boardTicks, [0.018, 0.012, i % 5 === 0 ? 0.18 : 0.09], [i * 0.48, -0.279, 4.99], tickMat);
    }
    this.labels = document.createElement('div');
    this.labels.className = 'room-label-layer';
    Object.assign(this.labels.style, { position: 'absolute', inset: '0', pointerEvents: 'none', overflow: 'hidden' });
    this.container.append(this.labels);
    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this._projection = new THREE.Vector3();
  }

  _createPod(room) {
    const group = new THREE.Group();
    group.position.copy(positionForSlot(room.slot));
    this.scene.add(group);
    const hull = this._material('hull', '#37454a');
    const wall = this._material('wall', '#536263', { roughness: 0.84 });
    const dark = this._material('furniture', '#24343a', { metalness: 0.5 });
    const top = this._material('deskTop', '#6a7672');
    const accent = this._material('accent', '#b0bc9a', { emissive: '#718766', emissiveIntensity: 0.13 });
    const screen = this._material('screen', '#88bdad', { emissive: '#86bca9', emissiveIntensity: 0.5 });
    const light = new THREE.MeshBasicMaterial({ color: PALETTE[room.status] || PALETTE.unknown });
    const glow = new THREE.MeshBasicMaterial({ color: PALETTE[room.status] || PALETTE.unknown, transparent: true, opacity: 0.15, depthWrite: false, side: THREE.DoubleSide });
    const slab = this._slab(group, 4.65, 3.8, 0.22, -0.22, hull, 'roomBase');
    slab.userData.sectorId = room.id;
    const floorMat = this._material('floor', '#334446', { metalness: 0.22 });
    this._slab(group, 4.4, 3.55, 0.055, 0.005, floorMat, 'roomFloor');
    const plate = this._material('floorInset', '#293a3c');
    for (let x = -1; x <= 1; x += 1) {
      for (let z = -1; z <= 1; z += 1) this._box(group, [1.32, 0.016, 1.02], [x * 1.38, 0.073, z * 1.08], plate);
    }
    // Cutaway walls leave the near edge open so evidence stays visible.
    this._box(group, [4.37, 0.92, 0.16], [0, 0.51, -1.68], wall, true);
    this._box(group, [0.16, 0.72, 2.3], [-2.12, 0.41, -0.45], wall, true);
    this._box(group, [0.16, 0.35, 1.25], [2.12, 0.23, -0.97], wall, true);
    this._box(group, [1.23, 0.22, 0.16], [-1.5, 0.17, 1.68], hull);
    this._box(group, [0.78, 0.22, 0.16], [1.71, 0.17, 1.68], hull);
    this._box(group, [4.38, 0.045, 0.19], [0, 0.99, -1.68], dark);
    this._box(group, [0.2, 0.04, 2.3], [-2.12, 0.79, -0.45], dark);
    this._box(group, [3.65, 0.025, 0.03], [0.1, 0.83, -1.58], light);
    this._box(group, [0.03, 0.025, 1.7], [-2.015, 0.64, -0.45], accent);

    // Status strips are physical details, with a subtle floor wash beneath.
    this._box(group, [3.93, 0.026, 0.045], [0, 0.093, 1.51], light);
    this._box(group, [0.045, 0.026, 2.93], [2.01, 0.093, 0], light);
    this._box(group, [0.045, 0.026, 2.93], [-2.01, 0.093, 0], light);
    const wash = new THREE.Mesh(this._geometry('wash', () => new THREE.PlaneGeometry(4.18, 3.28)), glow);
    wash.rotation.x = -Math.PI / 2;
    wash.position.y = 0.087;
    group.add(wash);

    // Shared instrumentation; furnishings are illustrative schematic details.
    this._box(group, [1.1, 0.32, 0.54], [-1.21, 0.23, -1.16], dark, true);
    this._box(group, [1.19, 0.07, 0.62], [-1.21, 0.43, -1.16], top);
    this._box(group, [0.65, 0.31, 0.06], [-1.21, 0.63, -1.33], dark);
    this._box(group, [0.55, 0.23, 0.015], [-1.21, 0.64, -1.291], screen);
    this._box(group, [0.32, 0.015, 0.14], [-1.21, 0.478, -0.99], dark);
    this._box(group, [0.4, 0.6, 0.55], [1.65, 0.38, -1.16], dark, true);
    for (let i = 0; i < 4; i += 1) {
      this._box(group, [0.29, 0.035, 0.013], [1.65, 0.23 + i * 0.11, -0.877], i === 2 ? light : top);
    }
    for (const x of [-1.94, 1.94]) {
      this._box(group, [0.13, 0.15, 0.13], [x, 0.15, 1.43], dark);
      this._box(group, [0.085, 0.018, 0.085], [x, 0.24, 1.43], light);
    }

    const identity = `${room.id} ${room.label}`.toLowerCase();
    if (/coffee|cater|cafe|kitchen/.test(identity)) {
      this._box(group, [1.65, 0.47, 0.52], [0.38, 0.32, -1.12], dark, true);
      this._box(group, [1.75, 0.065, 0.65], [0.38, 0.59, -1.12], top);
      this._box(group, [0.3, 0.35, 0.3], [0.68, 0.79, -1.13], dark, true);
      this._box(group, [0.18, 0.06, 0.05], [0.68, 0.83, -0.968], light);
      for (const [x, z] of [[-0.91, 0.5], [0.9, 0.7]]) {
        const table = new THREE.Mesh(this._geometry('table', () => new THREE.CylinderGeometry(0.4, 0.4, 0.07, 12)), top);
        table.position.set(x, 0.57, z);
        table.castShadow = true;
        group.add(table);
        this._box(group, [0.08, 0.46, 0.08], [x, 0.32, z], dark);
        this._box(group, [0.31, 0.28, 0.31], [x + 0.5, 0.23, z], dark, true);
      }
    } else if (/stage|auditor|presentation/.test(identity)) {
      this._box(group, [1.8, 0.21, 1.07], [0.22, 0.2, -0.75], dark, true);
      this._box(group, [1.73, 0.018, 0.03], [0.22, 0.32, -0.2], light);
      this._box(group, [1.66, 0.72, 0.045], [0.22, 1.01, -1.42], dark);
      this._box(group, [1.5, 0.57, 0.01], [0.22, 1.01, -1.392], screen);
      for (const x of [-0.9, 0, 0.9]) {
        this._box(group, [0.48, 0.23, 0.42], [x, 0.22, 0.75], dark);
        this._box(group, [0.48, 0.31, 0.07], [x, 0.45, 0.94], top);
      }
    } else if (/entrance|lobby|reception/.test(identity)) {
      this._box(group, [1.8, 0.53, 0.65], [0.1, 0.36, -0.84], dark, true);
      this._box(group, [1.92, 0.07, 0.73], [0.1, 0.66, -0.84], top);
      for (const x of [-0.8, 0.8]) {
        this._box(group, [0.14, 0.72, 0.14], [x, 0.44, 0.85], dark);
        this._box(group, [0.04, 0.065, 0.24], [x, 0.82, 0.85], light);
      }
      this._box(group, [0.82, 0.04, 0.38], [0, 0.09, 1.22], accent);
    } else {
      this._box(group, [1.28, 0.36, 0.89], [0.28, 0.28, 0.22], dark, true);
      this._box(group, [1.4, 0.05, 1], [0.28, 0.49, 0.22], top);
      this._box(group, [0.55, 0.018, 0.38], [0.28, 0.529, 0.22], screen);
      this._box(group, [0.62, 0.37, 0.58], [-1.19, 0.27, 0.84], dark, true);
      this._box(group, [0.55, 0.04, 0.5], [-1.19, 0.48, 0.84], top);
      this._box(group, [0.42, 0.15, 0.42], [1.26, 0.17, 0.98], dark, true);
    }

    // Thin orbital beacon marks reported anomalies, not a physical entity.
    const beacon = new THREE.Group();
    beacon.position.set(0, 0.16, 0);
    const halo = new THREE.Mesh(this._geometry('halo', () => new THREE.TorusGeometry(0.49, 0.014, 5, 48)), light);
    halo.rotation.x = -Math.PI / 2;
    halo.position.y = 0.11;
    beacon.add(halo);
    const beam = new THREE.Mesh(this._geometry('beam', () => new THREE.CylinderGeometry(0.22, 0.48, 1.8, 12, 1, true)), glow);
    beam.position.y = 1.02;
    beacon.add(beam);
    const diamond = new THREE.Mesh(this._geometry('diamond', () => new THREE.OctahedronGeometry(0.19)), light);
    diamond.position.y = 1.88;
    beacon.add(diamond);
    group.add(beacon);
    const selection = new THREE.LineLoop(
      this._geometry('selection', () => new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(-2.42, 0.016, -1.99), new THREE.Vector3(2.42, 0.016, -1.99),
        new THREE.Vector3(2.42, 0.016, 1.99), new THREE.Vector3(-2.42, 0.016, 1.99),
      ])), new THREE.LineBasicMaterial({ color: '#d9f7bc', transparent: true, opacity: 0.95 }),
    );
    group.add(selection);
    const hit = new THREE.Mesh(this._geometry('hit', () => new THREE.BoxGeometry(4.6, 1.3, 3.8)), new THREE.MeshBasicMaterial({ visible: false }));
    hit.position.y = 0.58;
    hit.userData.sectorId = room.id;
    group.add(hit);

    const label = document.createElement('button');
    label.type = 'button';
    label.className = 'room-label';
    Object.assign(label.style, { position: 'absolute', pointerEvents: 'auto', transform: 'translate(-50%, -50%)' });
    const code = document.createElement('span');
    code.className = 'room-label-code';
    code.textContent = `S${String(room.slot + 1).padStart(2, '0')}`;
    const name = document.createElement('span');
    name.className = 'room-label-name';
    const status = document.createElement('span');
    status.className = 'room-label-status';
    label.append(code, name, status);
    label.addEventListener('click', () => this._userSelect(room.id));
    this.labels.append(label);
    return { group, light, glow, beacon, halo, diamond, selection, hit, label, name, status, room, ownedMaterials: [light, glow, selection.material, hit.material] };
  }

  _bindInteractions() {
    this._down = (event) => { this.downPoint = { x: event.clientX, y: event.clientY }; };
    this._up = (event) => {
      if (!this.downPoint || event.button !== 0 || Math.hypot(event.clientX - this.downPoint.x, event.clientY - this.downPoint.y) > 6) return;
      this.downPoint = null;
      const rect = this.canvas.getBoundingClientRect();
      this.pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
      this.raycaster.setFromCamera(this.pointer, this.camera);
      const hit = this.raycaster.intersectObjects([...this.items.values()].map((item) => item.hit), false)[0];
      if (hit) this._userSelect(hit.object.userData.sectorId);
    };
    this.canvas.addEventListener('pointerdown', this._down);
    this.canvas.addEventListener('pointerup', this._up);
  }

  _userSelect(id) {
    this.select(id);
    this.onSelect?.(id);
  }

  update(rooms = [], selectedId = this.selectedId) {
    if (this.disposed) return;
    const entries = rooms instanceof Map ? [...rooms.values()] : Array.isArray(rooms) ? rooms : Object.values(rooms || {});
    const next = new Map();
    entries.forEach((raw, index) => {
      const id = String(raw.id ?? raw.sector ?? `sector_${index}`);
      const prior = this.rooms.get(id);
      const slot = Number.isInteger(raw.slot) && raw.slot >= 0 ? raw.slot : prior?.slot ?? index;
      next.set(id, { ...raw, id, slot, label: String(raw.label || id.replaceAll('_', ' ')), status: PALETTE[raw.status] ? raw.status : 'unknown' });
    });
    this.rooms = next;
    for (const [id, item] of this.items) {
      if (!next.has(id)) {
        this.scene?.remove(item.group);
        item.label?.remove();
        item.fallback?.remove();
        item.ownedMaterials?.forEach((material) => material.dispose());
        this.items.delete(id);
      }
    }
    if (!this.webgl) {
      this._updateFallback();
      this.select(selectedId);
      return;
    }
    for (const room of next.values()) {
      let item = this.items.get(room.id);
      if (!item) {
        item = this._createPod(room);
        this.items.set(room.id, item);
      }
      item.room = room;
      item.group.position.copy(positionForSlot(room.slot));
      item.light.color.set(PALETTE[room.status]);
      item.glow.color.set(PALETTE[room.status]);
      item.glow.opacity = room.status === 'breached' ? 0.18 : room.status === 'hot' ? 0.12 : 0.045;
      item.beacon.visible = room.status === 'breached' || room.status === 'hot';
      item.name.textContent = room.label;
      item.status.textContent = STATE_LABEL[room.status];
      item.label.dataset.state = room.status;
      item.label.className = `room-label is-${room.status}`;
      item.label.title = `${room.label}: ${STATE_LABEL[room.status]}. Select sector.`;
      item.label.style.setProperty('--room-accent', PALETTE[room.status]);
      item.group.traverse((object) => {
        if (object.material && object.material === item.light) object.material.opacity = room.status === 'stale' ? 0.4 : 1;
      });
    }
    this._resizeBoard();
    this.select(selectedId);
    this._renderFrame(performance.now());
  }

  _resizeBoard() {
    const maxSlot = Math.max(5, ...[...this.rooms.values()].map((room) => room.slot));
    const rows = Math.floor(maxSlot / 3) + 1;
    const depth = rows * SLOT_Z + 1.5;
    const centerZ = ((rows - 1) * SLOT_Z - SLOT_Z) / 2;
    this.boardBody.scale.z = depth;
    this.boardTop.scale.z = depth - 0.25;
    this.boardBase.scale.z = depth - 0.35;
    for (const board of [this.boardBody, this.boardTop, this.boardBase, this.boardGrid]) board.position.z = centerZ;
    this.boardGrid.scale.z = (depth - 0.6) / 15.5;
    this.boardFront.position.z = centerZ + depth / 2 + 0.01;
    this.boardBack.position.z = centerZ - depth / 2 - 0.01;
    this.boardTicks.position.z = centerZ + depth / 2 - 5.31;
    this.controls.maxDistance = Math.max(65, depth * 3);
  }

  select(id, { focus = false } = {}) {
    this.selectedId = id == null ? null : String(id);
    for (const [key, item] of this.items) {
      const selected = key === this.selectedId;
      if (item.selection) item.selection.visible = selected;
      const target = item.label || item.fallback;
      target?.classList.toggle('is-selected', selected);
      target?.setAttribute('aria-pressed', String(selected));
    }
    if (focus) this.focus(this.selectedId);
  }

  _moveCamera(position, target) {
    if (!this.webgl) return;
    if (!this.motion) {
      this.transition = null;
      this.controls.target.copy(target);
      this.camera.position.copy(position);
      this.controls.update();
      return;
    }
    this.transition = { position, target };
  }

  overview() {
    if (!this.webgl) {
      this.fallbackRoot?.scrollIntoView({ block: 'nearest', behavior: 'auto' });
      return;
    }
    const slots = [...this.rooms.values()].map((room) => room.slot);
    const rows = Math.floor(Math.max(5, ...slots) / 3) + 1;
    const center = new THREE.Vector3(0, 0, ((rows - 1) * SLOT_Z - SLOT_Z) / 2);
    const span = Math.max(16.5, rows * SLOT_Z + 2);
    const aspect = this.camera.aspect || 1;
    const distance = Math.max(span * 1.65, 22 / Math.max(aspect, 0.6));
    const direction = this.view === 'top' ? new THREE.Vector3(0, 1, 0.015) : new THREE.Vector3(0.68, 0.93, 1.14).normalize();
    this._moveCamera(center.clone().addScaledVector(direction, distance), center);
  }

  focus(id = this.selectedId) {
    const item = this.items.get(String(id));
    if (!item) return;
    if (!this.webgl) {
      item.fallback?.focus({ preventScroll: true });
      item.fallback?.scrollIntoView({ block: 'nearest', behavior: this.motion ? 'smooth' : 'auto' });
      return;
    }
    const center = item.group.position.clone().add(new THREE.Vector3(0, 0.2, 0));
    const direction = this.view === 'top' ? new THREE.Vector3(0, 1, 0.015) : this.camera.position.clone().sub(this.controls.target).normalize();
    this._moveCamera(center.clone().addScaledVector(direction, 11), center);
  }

  setView(view) {
    this.view = view === 'top' ? 'top' : 'iso';
    if (!this.webgl) return;
    const center = this.controls.target.clone();
    const distance = this.camera.position.distanceTo(center);
    const direction = this.view === 'top' ? new THREE.Vector3(0, 1, 0.015) : new THREE.Vector3(0.68, 0.93, 1.14).normalize();
    this._moveCamera(center.clone().addScaledVector(direction, distance), center);
  }

  zoom(factor) {
    if (!this.webgl || !Number.isFinite(factor) || factor <= 0) return;
    const center = this.controls.target.clone();
    const offset = this.camera.position.clone().sub(center);
    offset.setLength(THREE.MathUtils.clamp(offset.length() * factor, this.controls.minDistance, this.controls.maxDistance));
    this._moveCamera(center.clone().add(offset), center);
  }

  setMotion(enabled) {
    this.motionRequested = Boolean(enabled);
    this.motion = this.motionRequested && !this.mediaQuery.matches;
    if (this.controls) this.controls.enableDamping = this.motion;
    if (!this.motion && this.transition) {
      this.camera.position.copy(this.transition.position);
      this.controls.target.copy(this.transition.target);
      this.transition = null;
      this.controls.update();
    }
  }

  _resize() {
    if (!this.webgl || this.disposed) return;
    const { width, height } = this.container.getBoundingClientRect();
    if (!width || !height) return;
    this.width = width;
    this.height = height;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
    this._renderFrame(performance.now());
  }

  _renderFrame(time) {
    if (!this.webgl || this.disposed || !this.camera || document.hidden) return;
    const delta = Math.min((time - (this._lastFrame || time)) / 1000, 0.05);
    this._lastFrame = time;
    if (this.motion) this._time += delta;
    if (this.transition) {
      const amount = 1 - Math.exp(-delta * 7);
      this.camera.position.lerp(this.transition.position, amount);
      this.controls.target.lerp(this.transition.target, amount);
      if (this.camera.position.distanceTo(this.transition.position) < 0.015 && this.controls.target.distanceTo(this.transition.target) < 0.015) {
        this.camera.position.copy(this.transition.position);
        this.controls.target.copy(this.transition.target);
        this.transition = null;
      }
    }
    this.controls.update();
    for (const item of this.items.values()) {
      const phase = this._time * 1.35 + item.room.slot * 0.8;
      item.diamond.rotation.y = this.motion ? phase * 0.6 : 0.5;
      item.diamond.position.y = 1.88 + (this.motion ? Math.sin(phase) * 0.09 : 0);
      const pulse = this.motion ? (Math.sin(phase * 1.8) + 1) / 2 : 0.35;
      item.halo.scale.setScalar(1 + pulse * 0.15);
      this._projection.copy(item.group.position).add(new THREE.Vector3(0, 0.23, 1.94)).project(this.camera);
      const visible = this._projection.z > -1 && this._projection.z < 1 && Math.abs(this._projection.x) < 1.16 && Math.abs(this._projection.y) < 1.16;
      item.label.hidden = !visible;
      item.label.style.left = `${(this._projection.x * 0.5 + 0.5) * (this.width || 1)}px`;
      item.label.style.top = `${(-this._projection.y * 0.5 + 0.5) * (this.height || 1)}px`;
      item.label.style.zIndex = String(Math.round((1 - this._projection.z) * 1000) + (item.room.id === this.selectedId ? 10 : 0));
    }
    this.renderer.render(this.scene, this.camera);
  }

  _fallback() {
    this.webgl = false;
    cancelAnimationFrame(this._raf);
    this.controls?.dispose();
    this.canvas?.remove();
    this.labels?.remove();
    this.resizeObserver?.disconnect();
    for (const item of this.items.values()) {
      item.ownedMaterials?.forEach((material) => material.dispose());
    }
    this.items.clear();
    this.container.classList.add('has-fallback');
    this.fallbackRoot = document.createElement('div');
    this.fallbackRoot.className = 'room-fallback';
    this.fallbackRoot.setAttribute('role', 'group');
    this.fallbackRoot.setAttribute('aria-label', 'Interactive sector schematic');
    this.container.append(this.fallbackRoot);
    this._updateFallback();
  }

  _updateFallback() {
    for (const room of this.rooms.values()) {
      let item = this.items.get(room.id);
      if (!item) {
        const button = document.createElement('button');
        button.type = 'button';
        const code = document.createElement('span');
        code.className = 'room-fallback-code';
        const title = document.createElement('strong');
        const status = document.createElement('span');
        status.className = 'room-fallback-status';
        const floor = document.createElement('span');
        floor.className = 'room-fallback-floor';
        floor.setAttribute('aria-hidden', 'true');
        button.append(code, floor, title, status);
        button.addEventListener('click', () => this._userSelect(room.id));
        this.fallbackRoot.append(button);
        item = { fallback: button, code, title, status, room };
        this.items.set(room.id, item);
      }
      item.room = room;
      item.fallback.className = `room-fallback-card is-${room.status}`;
      item.fallback.dataset.state = room.status;
      item.fallback.style.setProperty('--room-accent', PALETTE[room.status]);
      item.fallback.style.gridColumn = String(room.slot % 3 + 1);
      item.fallback.style.gridRow = String(Math.floor(room.slot / 3) + 1);
      item.code.textContent = `SECTOR ${String(room.slot + 1).padStart(2, '0')}`;
      item.title.textContent = room.label;
      item.status.textContent = STATE_LABEL[room.status];
    }
    this.select(this.selectedId);
  }

  dispose() {
    this.disposed = true;
    cancelAnimationFrame(this._raf);
    document.removeEventListener('visibilitychange', this._visibility);
    this.mediaQuery.removeEventListener?.('change', this._mediaChange);
    this.resizeObserver?.disconnect();
    this.controls?.dispose();
    this.canvas?.removeEventListener('pointerdown', this._down);
    this.canvas?.removeEventListener('pointerup', this._up);
    this.canvas?.removeEventListener('webglcontextlost', this._contextLost);
    this.geometries.forEach((geometry) => geometry.dispose());
    this.materials.forEach((material) => material.dispose());
    this.items.forEach((item) => item.ownedMaterials?.forEach((material) => material.dispose()));
    this.grid?.geometry.dispose();
    this.grid?.material.dispose();
    this.boardGrid?.geometry.dispose();
    this.boardGrid?.material.dispose();
    this.groundGeometry?.dispose();
    this.renderer?.dispose();
    this.canvas?.remove();
    this.labels?.remove();
    this.fallbackRoot?.remove();
  }
}
