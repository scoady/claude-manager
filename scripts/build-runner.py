#!/usr/bin/env python3
"""
Host-side build runner — runs on macOS, not in Docker.

Agents inside containers call this via:
  curl -X POST http://host.docker.internal:4050/build \
    -H 'Content-Type: application/json' \
    -d '{"cwd": "/path/to/project", "command": "make build"}'

Only accepts commands in ~/git/claude-managed-projects/ for safety.
"""

import json
import os
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PORT = 4050
ALLOWED_ROOT = Path.home() / "git" / "claude-managed-projects"


class BuildHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/build":
            self._respond(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
        except (json.JSONDecodeError, ValueError):
            self._respond(400, {"error": "invalid JSON"})
            return

        cwd = body.get("cwd", "")
        command = body.get("command", "")

        if not cwd or not command:
            self._respond(400, {"error": "cwd and command are required"})
            return

        # Safety: only allow commands in managed projects
        cwd_path = Path(cwd).resolve()
        if not str(cwd_path).startswith(str(ALLOWED_ROOT)):
            self._respond(403, {"error": f"cwd must be under {ALLOWED_ROOT}"})
            return

        if not cwd_path.is_dir():
            self._respond(400, {"error": f"directory not found: {cwd}"})
            return

        print(f"[build-runner] {cwd} $ {command}")

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(cwd_path),
                capture_output=True,
                text=True,
                timeout=300,
                env={**os.environ, "PATH": f"/usr/bin:/usr/local/bin:/opt/homebrew/bin:{os.environ.get('PATH', '')}"},
            )
            self._respond(200, {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            })
        except subprocess.TimeoutExpired:
            self._respond(504, {"error": "build timed out (300s)"})
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "ok", "allowed_root": str(ALLOWED_ROOT)})
            return
        self._respond(404, {"error": "not found"})

    def _respond(self, code, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[build-runner] {args[0]}")


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), BuildHandler)
    print(f"[build-runner] Listening on port {PORT}")
    print(f"[build-runner] Allowed root: {ALLOWED_ROOT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[build-runner] Shutting down")
        server.shutdown()
