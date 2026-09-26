#!/usr/bin/env python3
"""HTTP smoke acceptance for the LTL interlock service.

Centred on the "never raises the alarm" violating loop: the submitted
procedure can spin forever between positions where ``alarm`` never holds,
so ``F alarm`` is violated by an infinite execution even though every
finite replay looks fine.  Exits 0 on success, 1 on any failure.
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("APP_URL", "http://127.0.0.1:8000").rstrip("/")
FAILURES = []


def check(name, cond, detail=""):
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", name,
                         (" -- " + detail) if (detail and not cond) else ""),
          flush=True)
    if not cond:
        FAILURES.append(name)


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:
            return exc.code, {}
    except Exception as exc:  # connection refused etc.
        return 0, {"error": str(exc)}


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

ALWAYS_ALARMING = {
    "positions": ["normal", "alerting"],
    "transitions": [
        {"id": "t1", "from": "normal", "to": "alerting"},
        {"id": "t2", "from": "alerting", "to": "normal"},
    ],
    "initial": "normal",
    "labels": {"alerting": ["alarm"]},
    "formula": "F alarm",
}


def main():
    code, body = req("GET", "/healthz")
    check("health endpoint", code == 200 and body.get("status") == "ok",
          "got %s %s" % (code, body))

    code, listing = req("GET", "/reviews")
    check("list reviews", code == 200 and "ids" in listing, "got %s" % code)
    base_count = len(listing.get("ids", []))

    # ---- the never-alarm violating loop ----------------------------------
    code, created = req("POST", "/reviews", NEVER_ALARM)
    check("create never-alarm review", code == 201 and "id" in created,
          "got %s %s" % (code, created))
    review_id = created.get("id")
    if review_id:
        code, record = req("GET", "/reviews/" + review_id)
        result = record.get("result", {})
        check("never-alarm review is violated",
              code == 200 and result.get("status") == "violated",
              "got %s %s" % (code, record))
        cx = result.get("counterexample") or {}
        prefix, loop = cx.get("prefix") or [], cx.get("loop") or []
        check("counterexample has a non-empty repeating loop", len(loop) >= 1)
        if prefix and loop:
            check("loop closes at its start position",
                  loop[-1]["position"] == prefix[-1]["position"])
            check("loop never raises the alarm",
                  all(step["position"] != "alerting" for step in loop))
            edges = {t["id"]: (t["from"], t["to"])
                     for t in NEVER_ALARM["transitions"]}
            ok = prefix[0].get("position") == "normal"
            pos = prefix[0].get("position")
            for step in prefix[1:]:
                src, dst = edges.get(step.get("via_transition"), (None, None))
                ok = ok and (src, dst) == (pos, step.get("position"))
                pos = step.get("position")
            for step in loop:
                src, dst = edges.get(step.get("transition"), (None, None))
                ok = ok and (src, dst) == (pos, step.get("position"))
                pos = step.get("position")
            check("prefix+loop is a closed walk of the procedure",
                  ok and pos == prefix[-1]["position"])
        evidence = cx.get("evidence") or []
        check("subformula evidence for every trace step",
              len(evidence) == len(prefix) - 1 + len(loop))
        if evidence:
            check("formula is false at the start of the trace",
                  evidence[0]["subformulas"].get("F alarm") is False)
            check("alarm stays false along the whole loop",
                  all(e["subformulas"].get("alarm") is False
                      for e in evidence if e["phase"] == "loop"))

    # ---- a procedure that always alarms holds -----------------------------
    code, created = req("POST", "/reviews", ALWAYS_ALARMING)
    check("create always-alarm review", code == 201 and "id" in created,
          "got %s %s" % (code, created))
    if created.get("id"):
        code, record = req("GET", "/reviews/" + created["id"])
        result = record.get("result", {})
        check("always-alarm review holds",
              code == 200 and result.get("status") == "holds"
              and result.get("counterexample") is None,
              "got %s %s" % (code, record))

    # ---- invalid requests are located rejections without audits ----------
    dangling = dict(NEVER_ALARM)
    dangling["transitions"] = (NEVER_ALARM["transitions"]
                               + [{"id": "t9", "from": "alerting", "to": "void"}])
    dead_end = dict(NEVER_ALARM)
    dead_end["transitions"] = [t for t in NEVER_ALARM["transitions"]
                               if t["from"] != "alerting"]
    bad_formula = dict(NEVER_ALARM, formula="F (alarm")
    undeclared = dict(NEVER_ALARM, formula="F chaos")
    for name, bad in [("dangling endpoint", dangling),
                      ("dead-end position", dead_end),
                      ("illegal formula", bad_formula),
                      ("undeclared proposition", undeclared)]:
        code, body = req("POST", "/reviews", bad)
        check("reject %s with located details" % name,
              code == 400 and "details" in body and "id" not in body,
              "got %s %s" % (code, body))

    code, listing = req("GET", "/reviews")
    check("rejected requests created no audit",
          code == 200 and len(listing.get("ids", [])) == base_count + 2,
          "got %s" % listing)

    code, _ = req("GET", "/reviews/does-not-exist")
    check("unknown review id is 404", code == 404, "got %s" % code)

    if FAILURES:
        print("SMOKE FAILED: %d check(s) failed: %s"
              % (len(FAILURES), ", ".join(FAILURES)), flush=True)
        return 1
    print("SMOKE OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
