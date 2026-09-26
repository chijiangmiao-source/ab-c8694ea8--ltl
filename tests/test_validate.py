import copy
import unittest

from src.validate import validate_spec


def minimal_body():
    return {
        "positions": ["a", "b"],
        "transitions": [
            {"id": "t1", "from": "a", "to": "b"},
            {"id": "t2", "from": "b", "to": "a"},
        ],
        "initial": "a",
        "labels": {"b": ["alarm"]},
        "formula": "F alarm",
    }


def errors_of(body):
    spec, errors = validate_spec(body)
    assert spec is None
    return errors


class ValidTests(unittest.TestCase):
    def test_minimal_valid(self):
        spec, errors = validate_spec(minimal_body())
        self.assertEqual(errors, [])
        self.assertEqual(spec["initial"], "a")
        self.assertEqual(spec["labels"]["b"], frozenset({"alarm"}))
        self.assertEqual(spec["labels"]["a"], frozenset())

    def test_labels_optional(self):
        body = minimal_body()
        del body["labels"]
        body["formula"] = "G F alarm"  # still rejected: alarm undeclared
        _, errors = validate_spec(body)
        self.assertTrue(any(e["field"] == "formula" for e in errors))


class PositionTests(unittest.TestCase):
    def test_too_few_positions(self):
        body = dict(minimal_body(), positions=["a"])
        self.assertTrue(any("between 2 and 24" in e["message"]
                            for e in errors_of(body)))

    def test_too_many_positions(self):
        body = dict(minimal_body(), positions=["p%d" % i for i in range(25)])
        self.assertTrue(any("between 2 and 24" in e["message"]
                            for e in errors_of(body)))

    def test_duplicate_positions(self):
        body = dict(minimal_body(), positions=["a", "a"])
        errs = errors_of(body)
        self.assertTrue(any(e.get("duplicates") == ["a"] for e in errs))

    def test_empty_position_name(self):
        body = dict(minimal_body(), positions=["a", " "])
        self.assertTrue(any(e["field"] == "positions[1]" for e in errors_of(body)))


class TransitionTests(unittest.TestCase):
    def test_dangling_from_endpoint_located(self):
        body = minimal_body()
        body["transitions"][0]["from"] = "nowhere"
        errs = errors_of(body)
        hits = [e for e in errs if "dangling endpoint" in e["message"]]
        self.assertTrue(hits)
        self.assertEqual(hits[0]["transition_id"], "t1")
        self.assertEqual(hits[0]["position"], "nowhere")
        self.assertEqual(hits[0]["field"], "transitions[0].from")

    def test_dangling_to_endpoint_located(self):
        body = minimal_body()
        body["transitions"][1]["to"] = "ghost"
        errs = errors_of(body)
        hits = [e for e in errs if "dangling endpoint" in e["message"]]
        self.assertTrue(hits)
        self.assertEqual(hits[0]["transition_id"], "t2")

    def test_duplicate_transition_ids(self):
        body = minimal_body()
        body["transitions"][1]["id"] = "t1"
        errs = errors_of(body)
        self.assertTrue(any(e.get("duplicates") == ["t1"] for e in errs))

    def test_dead_end_position_located(self):
        body = minimal_body()
        body["transitions"] = [t for t in body["transitions"]
                               if t["from"] != "b"]
        errs = errors_of(body)
        hits = [e for e in errs if "dead-end position" in e["message"]]
        self.assertTrue(hits)
        self.assertEqual(hits[0]["position"], "b")

    def test_missing_transition_fields(self):
        body = minimal_body()
        body["transitions"].append({"id": "t3", "from": "a"})
        errs = errors_of(body)
        self.assertTrue(any(e["field"] == "transitions[2].to" for e in errs))


class InitialAndLabelTests(unittest.TestCase):
    def test_initial_not_declared(self):
        body = dict(minimal_body(), initial="zzz")
        errs = errors_of(body)
        self.assertTrue(any(e["field"] == "initial" and e.get("position") == "zzz"
                            for e in errs))

    def test_labels_for_unknown_position(self):
        body = minimal_body()
        body["labels"]["ghost"] = ["alarm"]
        errs = errors_of(body)
        self.assertTrue(any(e.get("position") == "ghost" for e in errs))

    def test_operator_keyword_as_proposition(self):
        body = minimal_body()
        body["labels"]["a"] = ["X"]
        errs = errors_of(body)
        self.assertTrue(any("operator keyword" in e["message"] for e in errs))

    def test_invalid_proposition_name(self):
        body = minimal_body()
        body["labels"]["a"] = ["not a name"]
        errs = errors_of(body)
        self.assertTrue(any("invalid atomic proposition" in e["message"]
                            for e in errs))


class FormulaTests(unittest.TestCase):
    def test_syntax_error_located(self):
        body = dict(minimal_body(), formula="F (alarm")
        errs = errors_of(body)
        hits = [e for e in errs if e["field"] == "formula"]
        self.assertTrue(hits)
        self.assertIn("offset", hits[0])

    def test_unexpected_character_located(self):
        body = dict(minimal_body(), formula="F alarm -> G alarm")
        errs = errors_of(body)
        hits = [e for e in errs if e["field"] == "formula"]
        self.assertTrue(hits)
        self.assertEqual(hits[0].get("token"), "-")

    def test_undeclared_proposition(self):
        body = dict(minimal_body(), formula="F chaos")
        errs = errors_of(body)
        hits = [e for e in errs if e["field"] == "formula"]
        self.assertTrue(hits)
        self.assertEqual(hits[0].get("propositions"), ["chaos"])

    def test_formula_must_be_string(self):
        body = dict(minimal_body(), formula=42)
        self.assertTrue(any(e["field"] == "formula" for e in errors_of(body)))

    def test_body_must_be_object(self):
        spec, errors = validate_spec([1, 2, 3])
        self.assertIsNone(spec)
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
