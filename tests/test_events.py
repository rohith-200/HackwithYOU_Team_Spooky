"""Run from the repository root: python -B -m unittest discover -s tests -v."""

import json
import os
from queue import Queue
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from src.events import emit


class EventEmitterTests(unittest.TestCase):
    def test_default_endpoint_json_timeout_and_response_cleanup(self):
        event = {"type": "agent_log", "tick_id": 142, "message": "Ghost detected \u2192 coffee bar"}
        with patch.dict(os.environ, {}, clear=True), patch("src.events.urlopen") as send:
            self.assertIsNone(emit(event))
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "http://localhost:8000/emit")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(request.data), event)
        self.assertEqual(send.call_args.kwargs["timeout"], 0.5)
        send.return_value.__exit__.assert_called_once()
        send.return_value.__enter__.return_value.read.assert_not_called()

    def test_url_is_read_at_call_time_and_trailing_slashes_are_removed(self):
        with patch("src.events.urlopen") as send:
            for base in (" http://127.0.0.1:8123/// ", "http://127.0.0.1:8124"):
                with self.subTest(base=base), patch.dict(os.environ, {"UI_URL": base}):
                    emit({"type": "decision"})
                    self.assertEqual(send.call_args.args[0].full_url, base.strip().rstrip("/") + "/emit")

    def test_blank_url_uses_default(self):
        with patch.dict(os.environ, {"UI_URL": " "}), patch("src.events.urlopen") as send:
            emit({"type": "score"})
        self.assertEqual(send.call_args.args[0].full_url, "http://localhost:8000/emit")

    def test_connection_http_and_timeout_failures_do_not_escape(self):
        errors = [URLError("offline"), TimeoutError("slow UI"),
                  HTTPError("http://localhost:8000/emit", 503, "Unavailable", {}, None)]
        for error in errors:
            with self.subTest(error=type(error).__name__), patch("src.events.urlopen", side_effect=error):
                self.assertIsNone(emit({"type": "metrics", "tick_id": 142}))

    def test_serialization_and_configuration_failures_do_not_escape(self):
        with patch("src.events.urlopen") as send:
            for event in ({"bad": object()}, {"cost_usd": float("nan")}):
                with self.subTest(event=event):
                    self.assertIsNone(emit(event))
            with patch.dict(os.environ, {"UI_URL": "not-a-url"}):
                self.assertIsNone(emit({"type": "tick_start"}))
        send.assert_not_called()


class LocalReceiverTests(unittest.TestCase):
    def setUp(self):
        self.received = Queue()
        received = self.received

        class Receiver(BaseHTTPRequestHandler):
            def do_POST(handler):
                data = handler.rfile.read(int(handler.headers.get("Content-Length", "0")))
                received.put((handler.path, json.loads(data)))
                if handler.server.delay:
                    time.sleep(handler.server.delay)
                try:
                    handler.send_response(204)
                    handler.end_headers()
                except OSError:
                    pass  # Expected if the emitter has already timed out.

            def log_message(handler, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
        self.server.daemon_threads = True
        self.server.delay = 0
        self.thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)

    def test_all_six_event_types_reach_a_real_receiver_unchanged(self):
        with patch.dict(os.environ, {"UI_URL": self.url, "NO_PROXY": "127.0.0.1"}):
            for event_type in ("tick_start", "agent_log", "decision", "score", "dispatch", "metrics"):
                event = {"type": event_type, "tick_id": 142, "details": {"sector": "catering_coffee_bar"}}
                with self.subTest(event_type=event_type):
                    self.assertIsNone(emit(event))
                    self.assertEqual(self.received.get(timeout=1), ("/emit", event))

    def test_slow_receiver_times_out_without_raising(self):
        self.server.delay = 1.5
        with patch.dict(os.environ, {"UI_URL": self.url, "NO_PROXY": "127.0.0.1"}):
            started = time.monotonic()
            self.assertIsNone(emit({"type": "decision", "tick_id": 142}))
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.25)
        self.assertEqual(self.received.get(timeout=1)[0], "/emit")


if __name__ == "__main__":
    unittest.main()
