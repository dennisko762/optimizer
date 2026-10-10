"""Local stand-in for the operator NOTAM / risk feeds (manual verification).

Serves the repo's Navigraph fixtures over HTTP so the M4 "with data" path
can be exercised without a Navigraph subscription or a real NOTAM
contract. Dev-only helper; nothing in crew_platform imports it.

    python scripts/serve_navigraph_fixtures.py 8099
    NAVIGRAPH_NOTAM_URL=http://127.0.0.1:8099/notams \
    NAVIGRAPH_RISK_URL=http://127.0.0.1:8099/risks  uvicorn ...
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures" / "navigraph"


class Handler(BaseHTTPRequestHandler):
    def _send(self, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        path = self.path.split("?")[0]
        if path == "/notams":
            raw = (FIXTURES / "notams_icao.txt").read_text(encoding="utf-8")
            self._send({"notams": [raw]})
        elif path == "/risks":
            self._send(json.loads((FIXTURES / "risk_bulletin.json").read_text("utf-8")))
        else:
            self.send_error(404, "unknown fixture route")

    def log_message(self, *args) -> None:  # keep the console quiet
        pass


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    print(f"navigraph fixture feed on http://127.0.0.1:{port} (/notams, /risks)")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
