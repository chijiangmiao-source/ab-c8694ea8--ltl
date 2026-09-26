"""LTL formulae: parsing, negation normal form and a generalized Büchi
automaton (GBA) construction.

Surface syntax
--------------
    formula    ::= or_expr
    or_expr    ::= and_expr ("|" and_expr)*
    and_expr   ::= until_expr ("&" until_expr)*
    until_expr ::= unary_expr ("U" until_expr)?        # right associative
    unary_expr ::= "!" unary_expr | "X" unary_expr
                 | "F" unary_expr | "G" unary_expr
                 | "(" or_expr ")" | IDENT

Precedence (tightest first): the unary operators ``! X F G``, then ``U``
(right associative), then ``&``, then ``|``.

Only declared atomic propositions may appear as IDENT.  The single letters
``X``, ``F``, ``G`` and ``U`` are reserved operator keywords; identifiers
such as ``Fault`` or ``x1`` are ordinary propositions.

The GBA is built with a Gerth/Peled/Vardi/Wolper style tableau over the
negation normal form (NNF) of the formula.  NNF introduces the release
operator ``R`` (``G f`` becomes ``false R f``, ``F f`` becomes ``true U f``).
"""

from __future__ import annotations

import re

KEYWORDS = frozenset({"X", "F", "G", "U"})
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Surface AST nodes (tuples):
#   ("ap", name) ("not", f) ("and", f, g) ("or", f, g)
#   ("X", f) ("F", f) ("G", f) ("U", f, g)
# NNF-only nodes:
#   ("notap", name) ("true",) ("false",) ("R", f, g)

# Safety bound on the tableau so a pathological formula cannot wedge the
# service.  This is a resource limit, not a replay-depth bound: the
# automaton, once built, is analysed exhaustively.
MAX_GBA_STATES = 20000


class FormulaError(Exception):
    """Syntax or semantic error in an LTL formula, located by text offset."""

    def __init__(self, message, position=None, token=None):
        super().__init__(message)
        self.message = message
        self.position = position
        self.token = token

    def to_dict(self):
        out = {"message": self.message}
        if self.position is not None:
            out["offset"] = self.position
        if self.token is not None:
            out["token"] = self.token
        return out


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _tokenize(text):
    tokens = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "!&|()":
            tokens.append((ch, ch, i))
            i += 1
            continue
        match = _IDENT_RE.match(text, i)
        if match:
            word = match.group(0)
            kind = word if word in KEYWORDS else "AP"
            tokens.append((kind, word, i))
            i = match.end()
            continue
        raise FormulaError("unexpected character %r" % ch, position=i, token=ch)
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.i = 0
        # offset just past the last token, used to locate end-of-input errors
        self.end_pos = (tokens[-1][2] + len(tokens[-1][1])) if tokens else 0

    def peek(self):
        if self.i < len(self.tokens):
            return self.tokens[self.i]
        return (None, None, None)

    def advance(self):
        tok = self.peek()
        self.i += 1
        return tok

    def expect(self, kind):
        kind0, text, pos = self.peek()
        if kind0 != kind:
            if kind0 is None:
                raise FormulaError(
                    "unexpected end of formula, expected %r" % kind,
                    position=self.end_pos)
            raise FormulaError(
                "expected %r but found %r" % (kind, text), position=pos, token=text)
        return self.advance()

    def parse(self):
        if not self.tokens:
            raise FormulaError("formula is empty", position=0)
        result = self.parse_or()
        kind, text, pos = self.peek()
        if kind is not None:
            raise FormulaError("unexpected trailing token %r" % text,
                               position=pos, token=text)
        return result

    def parse_or(self):
        left = self.parse_and()
        while self.peek()[0] == "|":
            self.advance()
            left = ("or", left, self.parse_and())
        return left

    def parse_and(self):
        left = self.parse_until()
        while self.peek()[0] == "&":
            self.advance()
            left = ("and", left, self.parse_until())
        return left

    def parse_until(self):
        left = self.parse_unary()
        if self.peek()[0] == "U":
            self.advance()
            left = ("U", left, self.parse_until())  # right associative
        return left

    def parse_unary(self):
        kind, _text, _pos = self.peek()
        if kind == "!":
            self.advance()
            return ("not", self.parse_unary())
        if kind in ("X", "F", "G"):
            self.advance()
            return (kind, self.parse_unary())
        return self.parse_primary()

    def parse_primary(self):
        kind, text, pos = self.peek()
        if kind == "AP":
            self.advance()
            return ("ap", text)
        if kind == "(":
            self.advance()
            inner = self.parse_or()
            self.expect(")")
            return inner
        if kind is None:
            raise FormulaError("unexpected end of formula",
                               position=self.end_pos)
        raise FormulaError("unexpected token %r" % text, position=pos, token=text)


def parse(text):
    """Parse an LTL formula string into an AST tuple."""
    if not isinstance(text, str):
        raise FormulaError("formula must be a string")
    return _Parser(_tokenize(text)).parse()


# ---------------------------------------------------------------------------
# Formula utilities
# ---------------------------------------------------------------------------

def children(f):
    k = f[0]
    if k in ("not", "X", "F", "G"):
        return (f[1],)
    if k in ("and", "or", "U"):
        return (f[1], f[2])
    return ()


def subformulas(f):
    """All subformulas of a surface AST, as a set."""
    out = set()
    stack = [f]
    while stack:
        g = stack.pop()
        if g in out:
            continue
        out.add(g)
        stack.extend(children(g))
    return out


def formula_size(f):
    size = 0
    stack = [f]
    while stack:
        g = stack.pop()
        size += 1
        stack.extend(children(g))
    return size


def aps_of(f):
    """Atomic propositions referenced by a surface AST."""
    out = set()
    stack = [f]
    while stack:
        g = stack.pop()
        if g[0] == "ap":
            out.add(g[1])
        else:
            stack.extend(children(g))
    return out


def to_str(f):
    """Canonical, fully unambiguous rendering of a surface AST."""
    k = f[0]
    if k == "ap":
        return f[1]
    if k == "not":
        return "!" + to_str(f[1])
    if k in ("X", "F", "G"):
        return k + " " + to_str(f[1])
    if k in ("and", "or", "U"):
        op = {"and": " & ", "or": " | ", "U": " U "}[k]
        return "(" + to_str(f[1]) + op + to_str(f[2]) + ")"
    raise ValueError("cannot render node %r" % (k,))


# ---------------------------------------------------------------------------
# Negation normal form
# ---------------------------------------------------------------------------

def nnf(f, neg=False):
    """Negation normal form: negations only in front of atomic propositions.

    ``neg=True`` yields the NNF of the formula's negation.  ``F``/``G`` are
    translated to ``U``/``R`` (release) so the tableau only needs those two
    fixpoint operators.
    """
    k = f[0]
    if k == "ap":
        return ("notap", f[1]) if neg else f
    if k == "not":
        return nnf(f[1], not neg)
    if k == "and":
        a, b = nnf(f[1], neg), nnf(f[2], neg)
        return ("or", a, b) if neg else ("and", a, b)
    if k == "or":
        a, b = nnf(f[1], neg), nnf(f[2], neg)
        return ("and", a, b) if neg else ("or", a, b)
    if k == "X":
        return ("X", nnf(f[1], neg))
    if k == "F":
        if neg:
            return ("R", ("false",), nnf(f[1], True))
        return ("U", ("true",), nnf(f[1], False))
    if k == "G":
        if neg:
            return ("U", ("true",), nnf(f[1], True))
        return ("R", ("false",), nnf(f[1], False))
    if k == "U":
        if neg:
            return ("R", nnf(f[1], True), nnf(f[2], True))
        return ("U", nnf(f[1], False), nnf(f[2], False))
    raise ValueError("unknown node %r" % (k,))


def closure(f):
    """Fisher-Ladner closure of an NNF formula (subformulas plus the
    X-wrapped fixpoint formulas used to carry obligations across states)."""
    cl = set()

    def visit(g):
        if g in cl:
            return
        cl.add(g)
        k = g[0]
        if k in ("and", "or", "U", "R"):
            visit(g[1])
            visit(g[2])
            if k in ("U", "R"):
                visit(("X", g))
        elif k == "X":
            visit(g[1])

    visit(f)
    return cl


# ---------------------------------------------------------------------------
# Generalized Büchi automaton
# ---------------------------------------------------------------------------

class GBA:
    """Generalized Büchi automaton over sets of atomic propositions.

    ``states[i]`` is the frozenset of NNF formulas that hold in state ``i``;
    ``edges[i]`` the frozenset of successor state ids; ``accepting`` holds one
    set of state ids per ``U`` formula of the closure (a run is accepting iff
    it visits every one of those sets infinitely often).
    """

    def __init__(self):
        self.states = []
        self.nexts = []
        self.edges = []
        self.initial = frozenset()
        self.accepting = []
        self.u_formulas = []
        self.pos_literals = []
        self.neg_literals = []

    def literals_ok(self, sid, label):
        """True iff the Kripke label (set of APs) satisfies the state's
        positive and negative propositional literals."""
        return self.pos_literals[sid] <= label and not (self.neg_literals[sid] & label)


def build_gba(formula):
    """Build a GBA accepting exactly the models of the NNF ``formula``."""
    cl = closure(formula)
    u_formulas = sorted((g for g in cl if g[0] == "U"), key=repr)
    gba = GBA()
    node_ids = {}
    pending = []

    def register(old, nxt):
        key = (frozenset(old), frozenset(nxt))
        sid = node_ids.get(key)
        if sid is None:
            if len(node_ids) >= MAX_GBA_STATES:
                raise FormulaError(
                    "formula is too complex: automaton state limit exceeded")
            sid = len(node_ids)
            node_ids[key] = sid
            gba.states.append(key[0])
            gba.nexts.append(key[1])
            gba.edges.append(None)
            pending.append(sid)
        return sid

    def expand(todo, old, nxt):
        """Tableau expansion; returns the frozenset of state ids whose
        Now-set satisfies the pending obligations."""
        if not todo:
            return frozenset((register(old, nxt),))
        todo = list(todo)
        f = todo.pop()
        if f in old:
            return expand(todo, old, nxt)
        k = f[0]
        if k == "true":
            return expand(todo, old, nxt)
        if k == "false":
            return frozenset()
        if k == "ap":
            if ("notap", f[1]) in old:
                return frozenset()
            return expand(todo, old | {f}, nxt)
        if k == "notap":
            if ("ap", f[1]) in old:
                return frozenset()
            return expand(todo, old | {f}, nxt)
        if k == "and":
            return expand(todo + [f[1], f[2]], old | {f}, nxt)
        if k == "or":
            return (expand(todo + [f[1]], old | {f}, nxt)
                    | expand(todo + [f[2]], old | {f}, nxt))
        if k == "U":
            # a U b  <=>  b | (a & X(a U b))
            return (expand(todo + [f[2]], old | {f}, nxt)
                    | expand(todo + [f[1]], old | {f}, nxt | {f}))
        if k == "R":
            # a R b  <=>  b & (a | X(a R b))
            return (expand(todo + [f[1], f[2]], old | {f}, nxt)
                    | expand(todo + [f[2]], old | {f}, nxt | {f}))
        if k == "X":
            return expand(todo, old | {f}, nxt | {f[1]})
        raise ValueError("unexpected node %r in NNF" % (k,))

    gba.initial = expand([formula], frozenset(), frozenset())
    while pending:
        sid = pending.pop()
        gba.edges[sid] = expand(sorted(gba.nexts[sid], key=repr),
                                frozenset(), frozenset())

    for now in gba.states:
        gba.pos_literals.append(frozenset(f[1] for f in now if f[0] == "ap"))
        gba.neg_literals.append(frozenset(f[1] for f in now if f[0] == "notap"))
    gba.u_formulas = u_formulas
    for u in u_formulas:
        gba.accepting.append(frozenset(
            i for i, now in enumerate(gba.states) if u[2] in now or u not in now))
    return gba
