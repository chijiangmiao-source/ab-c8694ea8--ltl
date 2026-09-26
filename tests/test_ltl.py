import unittest

from src.ltl import (FormulaError, aps_of, build_gba, closure, nnf, parse,
                     to_str)


class ParseTests(unittest.TestCase):
    def test_precedence_unary_tightest(self):
        self.assertEqual(parse("!a & X b"),
                         ("and", ("not", ("ap", "a")), ("X", ("ap", "b"))))

    def test_precedence_until_over_and_or(self):
        self.assertEqual(parse("a U b | c"),
                         ("or", ("U", ("ap", "a"), ("ap", "b")), ("ap", "c")))
        self.assertEqual(parse("a | b & c"),
                         ("or", ("ap", "a"),
                          ("and", ("ap", "b"), ("ap", "c"))))

    def test_until_right_associative(self):
        self.assertEqual(parse("a U b U c"),
                         ("U", ("ap", "a"), ("U", ("ap", "b"), ("ap", "c"))))

    def test_unary_chains(self):
        self.assertEqual(parse("G F p"), ("G", ("F", ("ap", "p"))))
        self.assertEqual(parse("!!p"), ("not", ("not", ("ap", "p"))))
        self.assertEqual(parse("F G X p"),
                         ("F", ("G", ("X", ("ap", "p")))))

    def test_parentheses(self):
        self.assertEqual(parse("(a | b) U c"),
                         ("U", ("or", ("ap", "a"), ("ap", "b")), ("ap", "c")))

    def test_keyword_like_identifiers_are_propositions(self):
        self.assertEqual(parse("G Fault"), ("G", ("ap", "Fault")))
        self.assertEqual(aps_of(parse("F x1 & G up")), {"x1", "up"})

    def test_errors_are_located(self):
        for bad in ["(a", "a &", "a b", "", "a -> b", "F (a U)"]:
            with self.assertRaises(FormulaError) as ctx:
                parse(bad)
            self.assertIsNotNone(ctx.exception.message)

    def test_to_str_roundtrip(self):
        for text in ["G (!req | F ack)", "a U (b U c)", "!(X p)",
                     "F (a & G !b)"]:
            self.assertEqual(parse(to_str(parse(text))), parse(text))


class NnfTests(unittest.TestCase):
    def test_negation_pushed_to_literals(self):
        self.assertEqual(nnf(parse("!(a U b)")),
                         ("R", ("notap", "a"), ("notap", "b")))
        self.assertEqual(nnf(parse("!(a & b)")),
                         ("or", ("notap", "a"), ("notap", "b")))
        self.assertEqual(nnf(parse("!X p")), ("X", ("notap", "p")))
        self.assertEqual(nnf(parse("!!p")), ("ap", "p"))

    def test_temporal_duals(self):
        # not F p  ==  G !p  ==  false R !p
        self.assertEqual(nnf(parse("F p"), neg=True),
                         ("R", ("false",), ("notap", "p")))
        # not G p  ==  F !p  ==  true U !p
        self.assertEqual(nnf(parse("G p"), neg=True),
                         ("U", ("true",), ("notap", "p")))
        self.assertEqual(nnf(parse("F p")),
                         ("U", ("true",), ("ap", "p")))
        self.assertEqual(nnf(parse("G p")),
                         ("R", ("false",), ("ap", "p")))

    def test_closure_has_next_wrappers(self):
        f = nnf(parse("F p"))
        self.assertIn(("X", f), closure(f))


class GbaTests(unittest.TestCase):
    def test_negated_eventually_requires_alarm_absent(self):
        # GBA of not(F alarm) == G !alarm: every initial state forbids alarm
        gba = build_gba(nnf(parse("F alarm"), neg=True))
        self.assertTrue(gba.initial)
        for sid in gba.initial:
            self.assertIn("alarm", gba.neg_literals[sid])
        # pure safety property: no acceptance sets
        self.assertEqual(gba.accepting, [])

    def test_eventually_has_one_acceptance_set(self):
        gba = build_gba(nnf(parse("F alarm")))
        self.assertEqual(len(gba.accepting), 1)
        # some initial state already satisfies the goal (alarm now)
        self.assertTrue(any(s in gba.accepting[0] for s in gba.initial))

    def test_automaton_edges_computed(self):
        gba = build_gba(nnf(parse("G (!req | F ack)"), neg=True))
        self.assertTrue(gba.initial)
        self.assertTrue(all(edges is not None for edges in gba.edges))


if __name__ == "__main__":
    unittest.main()
