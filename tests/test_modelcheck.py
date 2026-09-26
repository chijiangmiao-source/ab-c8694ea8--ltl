import unittest

from src.ltl import parse, to_str
from src.modelcheck import eval_lasso_many, verify_spec
from src.validate import validate_spec

# A procedure that may spin forever between "normal" and "degraded" without
# ever raising "alarm" (only "alerting" declares it).  Any finite replay of
# this procedure looks innocent; only an accepting cycle shows that
# "F alarm" can be postponed forever.
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

# Requests must be acknowledged; "wait" may loop forever without "ack".
REQ_ACK = {
    "positions": ["idle", "wait", "done"],
    "transitions": [
        {"id": "t1", "from": "idle", "to": "wait"},
        {"id": "t2", "from": "wait", "to": "done"},
        {"id": "t3", "from": "done", "to": "idle"},
        {"id": "t4", "from": "wait", "to": "wait"},
    ],
    "initial": "idle",
    "labels": {"wait": ["req"], "done": ["ack"]},
    "formula": "G (!req | F ack)",
}

REQ_ACK_SAFE = {
    "positions": ["idle", "wait", "done"],
    "transitions": [
        {"id": "t1", "from": "idle", "to": "wait"},
        {"id": "t2", "from": "wait", "to": "done"},
        {"id": "t3", "from": "done", "to": "idle"},
    ],
    "initial": "idle",
    "labels": {"wait": ["req"], "done": ["ack"]},
    "formula": "G (!req | F ack)",
}

MAY_STAY = {
    "positions": ["s0", "s1"],
    "transitions": [
        {"id": "a", "from": "s0", "to": "s0"},
        {"id": "b", "from": "s0", "to": "s1"},
        {"id": "c", "from": "s1", "to": "s1"},
    ],
    "initial": "s0",
    "labels": {"s0": ["p"], "s1": ["q"]},
}


def verify_body(body):
    spec, errors = validate_spec(body)
    assert not errors, errors
    return verify_spec(spec)


def check_lasso_structure(testcase, body, cx):
    """prefix + loop must be a real closed walk of the submitted graph."""
    edges = {t["id"]: (t["from"], t["to"]) for t in body["transitions"]}
    prefix, loop = cx["prefix"], cx["loop"]
    testcase.assertEqual(prefix[0]["position"], body["initial"])
    testcase.assertIsNone(prefix[0]["via_transition"])
    for prev, step in zip(prefix, prefix[1:]):
        src, dst = edges[step["via_transition"]]
        testcase.assertEqual((src, dst), (prev["position"], step["position"]))
    testcase.assertTrue(loop, "loop must not be empty")
    pos = prefix[-1]["position"]
    for step in loop:
        src, dst = edges[step["transition"]]
        testcase.assertEqual((src, dst), (pos, step["position"]))
        pos = step["position"]
    testcase.assertEqual(pos, prefix[-1]["position"],
                         "loop must return to its start position")


def check_evidence_consistency(testcase, body, cx):
    """Every reported subformula truth value must satisfy the LTL semantics
    on the infinite lasso (local equations plus exact fixpoints checked
    against the atomic labels)."""
    ast = parse(body["formula"])
    labels = {p: set(v) for p, v in body.get("labels", {}).items()}
    evidence = cx["evidence"]
    n = len(evidence)
    loop_start = cx["loop_start_index"]
    testcase.assertEqual(n, len(cx["prefix"]) - 1 + len(cx["loop"]))
    succ = [i + 1 if i + 1 < n else loop_start for i in range(n)]

    def vals(f):
        return [e["subformulas"][to_str(f)] for e in evidence]

    def walk(f):
        k = f[0]
        v = vals(f)
        if k == "ap":
            for i, e in enumerate(evidence):
                testcase.assertEqual(v[i], f[1] in labels.get(e["position"], set()))
        for c in (f[1:] if k != "ap" else ()):
            walk(c)
        if k == "not":
            c = vals(f[1])
            for i in range(n):
                testcase.assertEqual(v[i], not c[i])
        elif k == "and":
            a, b = vals(f[1]), vals(f[2])
            for i in range(n):
                testcase.assertEqual(v[i], a[i] and b[i])
        elif k == "or":
            a, b = vals(f[1]), vals(f[2])
            for i in range(n):
                testcase.assertEqual(v[i], a[i] or b[i])
        elif k == "X":
            c = vals(f[1])
            for i in range(n):
                testcase.assertEqual(v[i], c[succ[i]])
        elif k == "F":
            c = vals(f[1])
            for i in range(n):
                testcase.assertEqual(v[i], c[i] or v[succ[i]])
        elif k == "G":
            c = vals(f[1])
            for i in range(n):
                testcase.assertEqual(v[i], c[i] and v[succ[i]])
        elif k == "U":
            a, b = vals(f[1]), vals(f[2])
            for i in range(n):
                testcase.assertEqual(v[i], b[i] or (a[i] and v[succ[i]]))

    walk(ast)
    # the trace is a counterexample: the formula itself fails at index 0
    testcase.assertFalse(vals(ast)[0])


class NeverAlarmLoopTests(unittest.TestCase):
    def test_violated_with_repeating_loop(self):
        result = verify_body(NEVER_ALARM)
        self.assertEqual(result["status"], "violated")
        cx = result["counterexample"]
        check_lasso_structure(self, NEVER_ALARM, cx)
        check_evidence_consistency(self, NEVER_ALARM, cx)

    def test_loop_never_raises_alarm(self):
        cx = verify_body(NEVER_ALARM)["counterexample"]
        loop_positions = {step["position"] for step in cx["loop"]}
        self.assertNotIn("alerting", loop_positions)
        for entry in cx["evidence"]:
            self.assertFalse(entry["subformulas"]["alarm"])
            self.assertFalse(entry["subformulas"]["F alarm"])

    def test_alarm_on_every_cycle_holds(self):
        result = verify_body(ALWAYS_ALARMING)
        self.assertEqual(result["status"], "holds")
        self.assertIsNone(result["counterexample"])
        self.assertGreater(result["stats"]["gba_states"], 0)


class NestedOperatorTests(unittest.TestCase):
    def test_request_without_ack_is_violated(self):
        result = verify_body(REQ_ACK)
        self.assertEqual(result["status"], "violated")
        cx = result["counterexample"]
        check_lasso_structure(self, REQ_ACK, cx)
        check_evidence_consistency(self, REQ_ACK, cx)
        # the violating loop waits forever without an acknowledgement
        self.assertEqual({s["position"] for s in cx["loop"]}, {"wait"})
        for entry in cx["evidence"]:
            if entry["phase"] == "loop":
                self.assertFalse(entry["subformulas"]["F ack"])
                self.assertFalse(entry["subformulas"]["G (!req | F ack)"])

    def test_request_always_acknowledged_holds(self):
        self.assertEqual(verify_body(REQ_ACK_SAFE)["status"], "holds")


class UntilAndNextTests(unittest.TestCase):
    def test_until_violated_by_stalling(self):
        body = dict(MAY_STAY, formula="p U q")
        result = verify_body(body)
        self.assertEqual(result["status"], "violated")
        check_evidence_consistency(self, body, result["counterexample"])

    def test_until_eventually_satisfied_holds(self):
        body = dict(MAY_STAY, formula="(G p) | (F q)")
        self.assertEqual(verify_body(body)["status"], "holds")

    def test_eventually_q_violated(self):
        body = dict(MAY_STAY, formula="F q")
        self.assertEqual(verify_body(body)["status"], "violated")

    def test_next_operator(self):
        body = dict(NEVER_ALARM, formula="X X alarm")
        result = verify_body(body)
        self.assertEqual(result["status"], "violated")
        check_evidence_consistency(self, body, result["counterexample"])


class LassoEvalTests(unittest.TestCase):
    def test_fixpoints_on_loop(self):
        labels = [{"p"}, set(), {"q"}]
        loop_start = 1
        vals = eval_lasso_many(
            [parse(s) for s in ["F p", "G p", "X p", "p U q", "F q", "G F p"]],
            labels, loop_start)
        self.assertEqual(vals[parse("F p")], [True, False, False])
        self.assertEqual(vals[parse("G p")], [False, False, False])
        self.assertEqual(vals[parse("X p")], [False, False, False])
        self.assertEqual(vals[parse("p U q")], [False, False, True])
        self.assertEqual(vals[parse("F q")], [True, True, True])
        self.assertEqual(vals[parse("G F p")], [False, False, False])

    def test_fixpoint_directions(self):
        # q never holds on the loop: F q must be the *least* fixpoint (false)
        labels = [{"p"}, {"p"}, {"p"}]
        vals = eval_lasso_many([parse("F q"), parse("G p"), parse("F G p")],
                               labels, 1)
        self.assertEqual(vals[parse("F q")], [False, False, False])
        self.assertEqual(vals[parse("G p")], [True, True, True])
        self.assertEqual(vals[parse("F G p")], [True, True, True])


if __name__ == "__main__":
    unittest.main()
