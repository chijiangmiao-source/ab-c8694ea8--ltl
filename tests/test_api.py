import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from src.server import ReviewStore, make_handler

NEVER_ALARM = {
    "positions": ["normal", "degraded", "alerting"],
    "transitions": [
        {"id": "t1", "from": "normal", "to": "degraded"},
        {"id": "t2", "from": "degraded", "to": "normal"},
        {"id": "t3", "from": "normal", "to": "alerting"},
        {"id": "t4", "from": "alerting", "to": "normal"},
        {"id": "t5", "from": "degraded", "to": "degraded"},
    ],
    "initial": "normal",
    "labels": {"alerting": ["alarm"]},
    "formula": "F alarm",
}


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                         make_handler(ReviewStore()))
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_health(self):
        code, body = self.request("GET", "/healthz")
        self.assertEqual((code, body.get("status")), (200, "ok"))

    def test_create_and_read_violated_review(self):
        code, created = self.request("POST", "/reviews", NEVER_ALARM)
        self.assertEqual(code, 201)
        self.assertEqual(created["status"], "violated")
        code, record = self.request("GET", "/reviews/" + created["id"])
        self.assertEqual(code, 200)
        self.assertEqual(record["result"]["status"], "violated")
        cx = record["result"]["counterexample"]
        self.assertTrue(cx["loop"])
        self.assertTrue(cx["evidence"])
        self.assertEqual(record["request"]["formula"], "F alarm")

    def test_create_and_read_holds_review(self):
        body = {
            "positions": ["normal", "alerting"],
            "transitions": [
                {"id": "t1", "from": "normal", "to": "alerting"},
                {"id": "t2", "from": "alerting", "to": "normal"},
            ],
            "initial": "normal",
            "labels": {"alerting": ["alarm"]},
            "formula": "F alarm",
        }
        code, created = self.request("POST", "/reviews", body)
        self.assertEqual((code, created["status"]), (201, "holds"))
        code, record = self.request("GET", created["review"])
        self.assertEqual(record["result"]["status"], "holds")
        self.assertIsNone(record["result"]["counterexample"])

    def test_invalid_request_creates_no_audit(self):
        _, before = self.request("GET", "/reviews")
        bad = dict(NEVER_ALARM)
        # remove every transition leaving "alerting": it becomes a dead-end
        bad["transitions"] = [t for t in NEVER_ALARM["transitions"]
                              if t["from"] != "alerting"]
        code, body = self.request("POST", "/reviews", bad)
        self.assertEqual(code, 400)
        self.assertNotIn("id", body)
        self.assertTrue(any("dead-end" in d["message"] for d in body["details"]))
        _, after = self.request("GET", "/reviews")
        self.assertEqual(len(after["ids"]), len(before["ids"]))

    def test_unknown_review_is_404(self):
        code, body = self.request("GET", "/reviews/nope")
        self.assertEqual(code, 404)
        self.assertIn("error", body)

    def test_malformed_json(self):
        req = urllib.request.Request(self.base + "/reviews",
                                     data=b"{not json", method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                code = resp.status
        except urllib.error.HTTPError as exc:
            code = exc.code
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
