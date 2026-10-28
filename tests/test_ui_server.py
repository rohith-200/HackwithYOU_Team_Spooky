"""C2 delivery tests: python -B -m unittest discover -s tests -v."""

import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import WebSocket
from fastapi.testclient import TestClient

from ui.mock_replay import sample_events
from ui.server import EventHub, create_app


class EventServerTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def test_index_is_served_from_another_working_directory(self):
        original_directory = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                response = self.client.get("/")
            finally:
                os.chdir(original_directory)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Watchtower", response.text)
        self.assertIn("new WebSocket", response.text)

    def test_emit_acknowledges_events_without_browsers(self):
        response = self.client.post("/emit", json={"type": "score", "tick_id": 142})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "clients": 0})

    def test_local_modules_and_vendor_assets_are_served(self):
        for asset in ("state.js", "scene.js", "demo.js", "style.css", "vendor/three/three.module.min.js", "vendor/three/three.core.js"):
            with self.subTest(asset=asset):
                response = self.client.get("/assets/" + asset)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.content)
        self.assertEqual(self.client.get("/assets/../server.py").status_code, 404)

    def test_two_browsers_receive_all_mock_events_unchanged_in_order(self):
        with self.client.websocket_connect("/ws") as first, self.client.websocket_connect("/ws") as second:
            for event in sample_events():
                response = self.client.post("/emit", json=event)
                self.assertEqual(response.json(), {"ok": True, "clients": 2})
                self.assertEqual(first.receive_json(), event)
                self.assertEqual(second.receive_json(), event)

    def test_partial_and_future_events_are_forwarded_with_unicode(self):
        with self.client.websocket_connect("/ws") as browser:
            for event in ({}, {"type": "future_event", "message": "\u2192 <script>not markup</script>"}):
                response = self.client.post("/emit", json=event)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(browser.receive_json(), event)

    def test_disconnected_browser_does_not_break_the_other_browser(self):
        with self.client.websocket_connect("/ws") as remaining:
            with self.client.websocket_connect("/ws"):
                self.assertEqual(len(self.app.state.hub.connections), 2)
            event = {"type": "decision", "tick_id": 143, "action": "observe"}
            response = self.client.post("/emit", json=event)
            self.assertEqual(response.json(), {"ok": True, "clients": 1})
            self.assertEqual(remaining.receive_json(), event)
        self.assertEqual(len(self.app.state.hub.connections), 0)

    def test_invalid_json_and_non_object_payloads_are_rejected(self):
        for payload in (None, [], "event", 1):
            with self.subTest(payload=payload):
                response = self.client.post("/emit", json=payload)
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.post("/emit", content="{broken", headers={"Content-Type": "application/json"}).status_code, 422)
        self.assertEqual(self.client.post("/emit", content='{"cost_usd":NaN}', headers={"Content-Type": "application/json"}).status_code, 422)

    def test_stalled_socket_does_not_delay_http_or_other_browsers(self):
        original_send = WebSocket.send_text

        async def send_text(socket, data):
            if socket.scope.get("query_string") == b"slow=1":
                await asyncio.Event().wait()
            else:
                await original_send(socket, data)

        with patch.object(WebSocket, "send_text", send_text):
            with self.client.websocket_connect("/ws?slow=1"), self.client.websocket_connect("/ws") as healthy:
                event = {"type": "agent_log", "message": "A slow tab cannot block delivery."}
                started = time.monotonic()
                response = self.client.post("/emit", json=event)
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertEqual(response.json(), {"ok": True, "clients": 2})
                self.assertEqual(healthy.receive_json(), event)


class EventHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_queue_disconnects_only_the_slow_client(self):
        hub = EventHub()
        slow = hub.add(object())
        healthy = hub.add(object())
        slow.outbox = asyncio.Queue(maxsize=1)
        event = {"type": "metrics", "tick_id": 142}
        self.assertEqual(hub.publish(event), 2)
        self.assertEqual(json.loads(healthy.outbox.get_nowait()), event)
        self.assertEqual(hub.publish(event), 1)
        self.assertTrue(slow.overloaded.is_set())
        self.assertNotIn(slow, hub.connections)
        self.assertIn(healthy, hub.connections)
        self.assertEqual(slow.outbox.qsize(), 1)
        self.assertEqual(json.loads(healthy.outbox.get_nowait()), event)


if __name__ == "__main__":
    unittest.main()
