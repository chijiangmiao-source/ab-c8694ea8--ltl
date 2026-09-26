"""HTTP interface for the LTL interlock review service (standard library only).

Endpoints
---------
POST /reviews        create a review; 201 returns the saved audit id, 400 a
                     located rejection (no audit record is created)
GET  /reviews/{id}   read the saved conclusion or the lasso counterexample
GET  /reviews        list saved audit ids
GET  /healthz        health check
"""

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .ltl import FormulaError
from .modelcheck import verify_spec
from .validate import validate_spec

MAX_BODY_BYTES = 1 << 20  # 1 MiB


class ReviewStore:
    """In-memory audit store: review id -> immutable review record."""

    def __init__(self):
        self._lock = threading.Lock()
        self._records = {}

    def add(self, record):
        with self._lock:
            self._records[record["id"]] = record

    def get(self, review_id):
        with self._lock:
            return self._records.get(review_id)

    def ids(self):
        with self._lock:
            return sorted(self._records)


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LTLInterlock/1.0"
        protocol_version = "HTTP/1.1"

        # -- helpers ------------------------------------------------------
        def _send_json(self, code, obj):
            data = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _path(self):
            path = self.path.split("?", 1)[0]
            if path != "/":
                path = path.rstrip("/")
            return path or "/"

        # -- routing --------------------------------------------------------
        def do_GET(self):
            path = self._path()
            if path == "/healthz":
                return self._send_json(200, {"status": "ok"})
            if path == "/":
                return self._send_json(200, {
                    "service": "LTL interlock review",
                    "endpoints": {
                        "POST /reviews": "create a review",
                        "GET /reviews/{id}": "read a saved review",
                        "GET /reviews": "list review ids",
                        "GET /healthz": "health check",
                    },
                })
            if path == "/reviews":
                return self._send_json(200, {"ids": store.ids()})
            if path.startswith("/reviews/"):
                review_id = path.rsplit("/", 1)[1]
                record = store.get(review_id)
                if record is None:
                    return self._send_json(404, {
                        "error": "review not found", "id": review_id})
                return self._send_json(200, record)
            return self._send_json(404, {"error": "not found"})

        def do_POST(self):
            path = self._path()
            if path != "/reviews":
                return self._send_json(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0:
                return self._send_json(400, {"error": "missing request body"})
            if length > MAX_BODY_BYTES:
                return self._send_json(413, {"error": "request body too large"})
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return self._send_json(400, {
                    "error": "request body is not valid JSON"})

            spec, errors = validate_spec(body)
            if errors:
                # Located rejection; nothing is stored, no audit is created.
                return self._send_json(400, {
                    "error": "invalid review request", "details": errors})
            try:
                result = verify_spec(spec)
            except FormulaError as exc:
                return self._send_json(400, {
                    "error": "cannot build automaton for formula",
                    "details": [exc.to_dict()]})
            except Exception:  # defensive: never drop the connection silently
                self.log_error("verification failed", exc_info=True)
                return self._send_json(500, {"error": "internal solver error"})

            review_id = uuid.uuid4().hex[:12]
            record = {
                "id": review_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "request": {
                    "positions": spec["positions"],
                    "transitions": spec["transitions"],
                    "initial": spec["initial"],
                    "labels": {p: sorted(spec["labels"][p])
                               for p in spec["positions"]},
                    "formula": spec["formula"],
                },
                "result": result,
            }
            store.add(record)
            return self._send_json(201, {
                "id": review_id,
                "status": result["status"],
                "review": "/reviews/%s" % review_id,
            })

    return Handler


def main():
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), make_handler(ReviewStore()))
    print("ltl-interlock listening on %s:%d" % (host, port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
