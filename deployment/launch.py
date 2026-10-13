"""Railway entrypoint that refuses unsafe configuration before bot startup."""

from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from deployment.preflight import validate_environment


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"status":"ok","mode":"health-only"}'
        self.send_response(200 if self.path in ("/", "/health") else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


def serve_health() -> None:
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()


def main() -> int:
    result = validate_environment(dict(os.environ))
    if not result.ok:
        for error in result.errors:
            print(f"preflight: {error}", file=sys.stderr)
        return 2
    if result.mode == "health-only":
        print("content-factory preflight ok: health-only")
        serve_health()
        return 0
    os.execv(sys.executable, [sys.executable, "main.py"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
