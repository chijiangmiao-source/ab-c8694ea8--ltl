"""Request validation.

Every problem is reported with a precise location (field, index, transition
id, position name or formula offset).  A request with any error is rejected
as a whole and never reaches the solver, so no audit record is created for
invalid input.
"""

import re
from collections import Counter

from .ltl import KEYWORDS, FormulaError, aps_of, parse

MIN_POSITIONS = 2
MAX_POSITIONS = 24
_AP_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_spec(body):
    """Validate a review request body.

    Returns ``(spec, [])`` on success, ``(None, errors)`` otherwise.
    """
    errors = []
    if not isinstance(body, dict):
        return None, [{"field": None,
                       "message": "request body must be a JSON object"}]

    # ---- positions: 2..24 unique names ----------------------------------
    raw_positions = body.get("positions")
    positions = []
    positions_ok = False
    if not isinstance(raw_positions, list):
        errors.append({"field": "positions",
                       "message": "positions must be an array of unique names"})
    else:
        if not MIN_POSITIONS <= len(raw_positions) <= MAX_POSITIONS:
            errors.append({
                "field": "positions",
                "message": ("positions must contain between %d and %d "
                            "entries, got %d"
                            % (MIN_POSITIONS, MAX_POSITIONS,
                               len(raw_positions)))})
        bad = [i for i, p in enumerate(raw_positions)
               if not isinstance(p, str) or not p.strip()]
        for i in bad:
            errors.append({"field": "positions[%d]" % i,
                           "message": "position name must be a non-empty string"})
        if not bad:
            dups = sorted(p for p, c in Counter(raw_positions).items() if c > 1)
            if dups:
                errors.append({"field": "positions",
                               "message": "position names must be unique",
                               "duplicates": dups})
            else:
                positions = list(raw_positions)
                positions_ok = True
    poset = set(positions)

    # ---- transitions: unique ids, endpoints must be declared ------------
    raw_transitions = body.get("transitions")
    transitions = []
    transitions_ok = False
    if not isinstance(raw_transitions, list):
        errors.append({"field": "transitions",
                       "message": ("transitions must be an array of "
                                   "{id, from, to} objects")})
    else:
        structurally_ok = True
        for i, t in enumerate(raw_transitions):
            if not isinstance(t, dict):
                errors.append({"field": "transitions[%d]" % i,
                               "message": ("transition must be an object with "
                                           "id, from and to")})
                structurally_ok = False
                continue
            item_ok = True
            tid = t.get("id")
            if not isinstance(tid, str) or not tid.strip():
                errors.append({"field": "transitions[%d].id" % i,
                               "message": "transition id must be a non-empty string"})
                item_ok = False
            for key in ("from", "to"):
                val = t.get(key)
                if not isinstance(val, str) or not val.strip():
                    errors.append({
                        "field": "transitions[%d].%s" % (i, key),
                        "message": "transition endpoint must be a non-empty string",
                        "transition_id": tid if isinstance(tid, str) else None})
                    item_ok = False
            if item_ok:
                transitions.append({"id": tid, "from": t["from"], "to": t["to"]})
            else:
                structurally_ok = False
        dup_ids = sorted(t for t, c in
                         Counter(x["id"] for x in transitions).items() if c > 1)
        if dup_ids:
            errors.append({"field": "transitions",
                           "message": "transition ids must be unique",
                           "duplicates": dup_ids})
            structurally_ok = False
        transitions_ok = structurally_ok

    if positions_ok and isinstance(raw_transitions, list):
        for i, t in enumerate(raw_transitions):
            if not isinstance(t, dict):
                continue
            tid = t.get("id")
            for key in ("from", "to"):
                val = t.get(key)
                if isinstance(val, str) and val.strip() and val not in poset:
                    errors.append({
                        "field": "transitions[%d].%s" % (i, key),
                        "message": ("dangling endpoint: position %r is not "
                                    "declared" % val),
                        "transition_id": tid if isinstance(tid, str) else None,
                        "position": val})

    # ---- dead-end positions ---------------------------------------------
    if positions_ok and transitions_ok:
        outgoing = Counter(t["from"] for t in transitions)
        for p in positions:
            if outgoing.get(p, 0) == 0:
                errors.append({
                    "field": "transitions",
                    "message": ("dead-end position: %r has no outgoing "
                                "transition" % p),
                    "position": p})

    # ---- initial position ------------------------------------------------
    initial = body.get("initial")
    if not isinstance(initial, str) or not initial.strip():
        errors.append({"field": "initial",
                       "message": "initial must be a declared position name"})
    elif positions_ok and initial not in poset:
        errors.append({"field": "initial",
                       "message": "initial position %r is not declared" % initial,
                       "position": initial})

    # ---- labels: per-position atomic propositions ------------------------
    raw_labels = body.get("labels", {})
    labels = {p: set() for p in positions}
    declared_aps = set()
    if not isinstance(raw_labels, dict):
        errors.append({"field": "labels",
                       "message": ("labels must be an object mapping position "
                                   "names to arrays of atomic propositions")})
    else:
        for pos_name, aps in raw_labels.items():
            if positions_ok and pos_name not in poset:
                errors.append({"field": "labels.%s" % pos_name,
                               "message": ("labels refer to undeclared position "
                                           "%r" % pos_name),
                               "position": pos_name})
                continue
            if not isinstance(aps, list):
                errors.append({"field": "labels.%s" % pos_name,
                               "message": ("atomic propositions must be an "
                                           "array of names")})
                continue
            for j, ap in enumerate(aps):
                field = "labels.%s[%d]" % (pos_name, j)
                if not isinstance(ap, str) or not _AP_RE.match(ap):
                    errors.append({"field": field,
                                   "message": ("invalid atomic proposition "
                                               "name %r" % (ap,))})
                elif ap in KEYWORDS:
                    errors.append({"field": field,
                                   "message": ("atomic proposition %r collides "
                                               "with an operator keyword" % ap)})
                else:
                    declared_aps.add(ap)
                    if pos_name in labels:
                        labels[pos_name].add(ap)

    # ---- formula ----------------------------------------------------------
    ast = None
    formula = body.get("formula")
    if not isinstance(formula, str) or not formula.strip():
        errors.append({"field": "formula",
                       "message": "formula must be a non-empty string"})
    else:
        try:
            ast = parse(formula)
        except FormulaError as exc:
            err = {"field": "formula", "message": exc.message}
            if exc.position is not None:
                err["offset"] = exc.position
            if exc.token is not None:
                err["token"] = exc.token
            errors.append(err)
        else:
            undeclared = sorted(aps_of(ast) - declared_aps)
            if undeclared:
                errors.append({
                    "field": "formula",
                    "message": "formula uses undeclared atomic propositions",
                    "propositions": undeclared})

    if errors:
        return None, errors
    return {
        "positions": positions,
        "transitions": transitions,
        "initial": initial,
        "labels": {p: frozenset(labels[p]) for p in positions},
        "formula": formula,
        "ast": ast,
    }, []
