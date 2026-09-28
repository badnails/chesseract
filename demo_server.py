#!/usr/bin/env python3
"""
Lightweight HTTP + SSE server for real-time chessboard visualization
during Demo Mode. Pushes board state after each gantry hop so the
browser animates piece movements in sync with hardware execution.
"""

from __future__ import annotations

import json
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn


def board_to_fen(board: dict[str, str]) -> str:
    rows = []
    for rank in range(7, -1, -1):
        empty = 0
        row = ""
        for f in range(8):
            sq = f"{chr(ord('a') + f)}{rank + 1}"
            piece = board.get(sq)
            if piece:
                if empty:
                    row += str(empty)
                    empty = 0
                row += piece
            else:
                empty += 1
        if empty:
            row += str(empty)
        rows.append(row)
    return "/".join(rows) + " w - - 0 1"


class _StateManager:
    def __init__(self):
        self._clients: list[queue.Queue] = []
        self._lock = threading.Lock()
        self.current: dict = {}

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=64)
        with self._lock:
            self._clients.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._clients.remove(q)
            except ValueError:
                pass

    def broadcast(self, state: dict) -> None:
        self.current = state
        with self._lock:
            dead = []
            for q in self._clients:
                try:
                    q.put_nowait(state)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._clients.remove(q)


class _Handler(BaseHTTPRequestHandler):
    state_mgr: _StateManager
    web_root: str
    on_difficulty_change = None

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == "/events":
            self._sse()
        elif self.path == "/api/state":
            self._api_state()
        else:
            self._static()

    def do_POST(self):
        if self.path == "/api/difficulty":
            self._set_difficulty()
        else:
            self.send_error(404)

    def _set_difficulty(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self.send_error(400)
            return
        preset = data.get("preset", "")
        callback = self.on_difficulty_change
        result = {"ok": False, "error": "No game running"}
        if callback:
            result = callback(preset)
        resp = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        q = self.state_mgr.subscribe()
        try:
            if self.state_mgr.current:
                self.wfile.write(f"data: {json.dumps(self.state_mgr.current)}\n\n".encode())
                self.wfile.flush()
            while True:
                state = q.get()
                self.wfile.write(f"data: {json.dumps(state)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.state_mgr.unsubscribe(q)

    def _api_state(self):
        body = json.dumps(self.state_mgr.current).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    _CT = {
        ".html": "text/html", ".css": "text/css",
        ".js": "application/javascript", ".json": "application/json",
        ".svg": "image/svg+xml", ".png": "image/png",
        ".woff": "font/woff", ".woff2": "font/woff2",
    }

    def _static(self):
        path = self.path.lstrip("/").split("?")[0] or "index.html"
        fp = os.path.join(self.web_root, path)
        if not os.path.isfile(fp):
            self.send_error(404)
            return
        ct = self._CT.get(os.path.splitext(fp)[1].lower(), "application/octet-stream")
        with open(fp, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class _Threaded(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class DemoServer:
    def __init__(self, port: int = 8080):
        self.port = port
        self._mgr = _StateManager()
        self._httpd: _Threaded | None = None

        _Handler.state_mgr = self._mgr
        _Handler.web_root = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "web_ui"
        )

    def start(self) -> bool:
        try:
            self._httpd = _Threaded(("0.0.0.0", self.port), _Handler)
        except OSError:
            return False
        t = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        t.start()
        return True

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()

    def set_difficulty_callback(self, callback) -> None:
        _Handler.on_difficulty_change = callback

    def push_state(self, state: dict) -> None:
        self._mgr.broadcast(state)

    def push_board_state(
        self,
        board: dict[str, str],
        last_move: dict | None = None,
        gantry_action: dict | None = None,
    ) -> None:
        self._mgr.broadcast({
            "fen": board_to_fen(board),
            "turn": "white",
            "last_move": last_move,
            "history": [],
            "demo_mode": True,
            "gantry_action": gantry_action,
        })
