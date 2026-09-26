"""Product model checking.

Pipeline (no replay depth, no random sampling, no label-only checks):

1. the formula is normalised (NNF) and its *negation* is compiled to a
   generalized Büchi automaton (GBA);
2. the GBA is multiplied with the procedure's Kripke structure (positions,
   labelled transitions, per-position atomic propositions);
3. reachable accepting cycles in the product are located exactly, via
   strongly connected components.  Any such cycle yields a lasso-shaped
   counterexample (finite prefix + infinitely repeated loop); if none
   exists, every infinite execution from the initial position satisfies
   the formula.
"""

from collections import deque

from .ltl import (build_gba, formula_size, nnf, subformulas, to_str)

METHOD = ("negation normal form -> generalized Buechi automaton of the "
          "negated formula -> synchronous product with the procedure graph "
          "-> reachable accepting cycle (SCC decomposition)")


# ---------------------------------------------------------------------------
# Lasso evaluation (exact truth of subformulas on prefix+loop traces)
# ---------------------------------------------------------------------------

def eval_lasso_many(formulas, labels_seq, loop_start):
    """Evaluate surface-AST formulas on an ultimately periodic trace.

    ``labels_seq[i]`` is the set of atomic propositions holding at trace
    index ``i``; indices ``loop_start .. n-1`` form the loop.  Temporal
    operators are solved by exact least/greatest fixpoint iteration over
    the finite lasso, so "eventually" and "always" are decided for the
    *infinite* unfolding, never truncated.
    """
    n = len(labels_seq)
    succ = tuple(i + 1 if i + 1 < n else loop_start for i in range(n))
    memo = {}

    def ev(f):
        cached = memo.get(f)
        if cached is not None:
            return cached
        k = f[0]
        if k == "ap":
            r = [f[1] in labels_seq[i] for i in range(n)]
        elif k == "not":
            c = ev(f[1])
            r = [not c[i] for i in range(n)]
        elif k == "and":
            a, b = ev(f[1]), ev(f[2])
            r = [a[i] and b[i] for i in range(n)]
        elif k == "or":
            a, b = ev(f[1]), ev(f[2])
            r = [a[i] or b[i] for i in range(n)]
        elif k == "X":
            c = ev(f[1])
            r = [c[succ[i]] for i in range(n)]
        elif k == "F":
            c = ev(f[1])
            r = list(c)
            while True:  # least fixpoint of r[i] = c[i] | r[succ(i)]
                new = [c[i] or r[succ[i]] for i in range(n)]
                if new == r:
                    break
                r = new
        elif k == "G":
            c = ev(f[1])
            r = list(c)
            while True:  # greatest fixpoint of r[i] = c[i] & r[succ(i)]
                new = [c[i] and r[succ[i]] for i in range(n)]
                if new == r:
                    break
                r = new
        elif k == "U":
            a, b = ev(f[1]), ev(f[2])
            r = list(b)
            while True:  # least fixpoint of r[i] = b[i] | (a[i] & r[succ(i)])
                new = [b[i] or (a[i] and r[succ[i]]) for i in range(n)]
                if new == r:
                    break
                r = new
        else:
            raise ValueError("cannot evaluate node %r" % (k,))
        memo[f] = r
        return r

    return {f: ev(f) for f in formulas}


# ---------------------------------------------------------------------------
# SCC decomposition (iterative Tarjan)
# ---------------------------------------------------------------------------

def _tarjan_sccs(adj):
    n = len(adj)
    index = [-1] * n
    low = [0] * n
    on_stack = [False] * n
    stack = []
    counter = 0
    sccs = []
    for start in range(n):
        if index[start] != -1:
            continue
        work = [(start, 0)]
        while work:
            v, pi = work[-1]
            if pi == 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on_stack[v] = True
            descended = False
            i = pi
            while i < len(adj[v]):
                w = adj[v][i][0]
                if index[w] == -1:
                    work[-1] = (v, i + 1)
                    work.append((w, 0))
                    descended = True
                    break
                if on_stack[w] and index[w] < low[v]:
                    low[v] = index[w]
                i += 1
            if descended:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                if low[v] < low[parent]:
                    low[parent] = low[v]
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    comp.append(w)
                    if w == v:
                        break
                sccs.append(comp)
    return sccs


def _bfs_path(adj, allowed, start, goal):
    """Edge path (list of (from, transition_id, to)) within ``allowed``."""
    if start == goal:
        return []
    prev = {start: None}
    queue = deque([start])
    while queue:
        u = queue.popleft()
        for v, tid in adj[u]:
            if v not in allowed or v in prev:
                continue
            prev[v] = (u, tid)
            if v == goal:
                path = []
                cur = v
                while prev[cur] is not None:
                    p, t = prev[cur]
                    path.append((p, t, cur))
                    cur = p
                path.reverse()
                return path
            queue.append(v)
    raise RuntimeError("SCC is not strongly connected (internal error)")


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_spec(spec):
    """Verify a validated specification; returns the review result dict."""
    ast = spec["ast"]
    gba = build_gba(nnf(ast, neg=True))
    positions = spec["positions"]
    labels = spec["labels"]
    initial = spec["initial"]

    out_edges = {p: [] for p in positions}
    for t in spec["transitions"]:
        out_edges[t["from"]].append((t["id"], t["to"]))

    # ---- synchronous product of the procedure graph with GBA(not phi) ----
    nodes = []    # product node id -> (position, gba state id)
    adj = []      # product node id -> [(product node id, transition id)]
    parent = []   # BFS tree: product node id -> (prev id, transition id)
    index = {}
    queue = deque()

    def add_node(state, par):
        index[state] = len(nodes)
        nodes.append(state)
        adj.append([])
        parent.append(par)
        queue.append(index[state])

    for sid in sorted(gba.initial):
        if gba.literals_ok(sid, labels[initial]):
            add_node((initial, sid), None)
    while queue:
        u = queue.popleft()
        pos, sid = nodes[u]
        for tid, pos2 in out_edges[pos]:
            for sid2 in sorted(gba.edges[sid]):
                if not gba.literals_ok(sid2, labels[pos2]):
                    continue
                state2 = (pos2, sid2)
                if state2 not in index:
                    add_node(state2, (u, tid))
                adj[u].append((index[state2], tid))

    stats = {
        "positions": len(positions),
        "transitions": len(spec["transitions"]),
        "gba_states": len(gba.states),
        "gba_acceptance_sets": len(gba.accepting),
        "product_states": len(nodes),
        "product_edges": sum(len(a) for a in adj),
    }

    # ---- reachable accepting cycle search over the product ---------------
    for comp in _tarjan_sccs(adj):
        comp_set = frozenset(comp)
        if len(comp) == 1 and not any(v == comp[0] for v, _ in adj[comp[0]]):
            continue  # trivial SCC without a self-loop is not a cycle
        if not all(any(nodes[u][1] in acc for u in comp)
                   for acc in gba.accepting):
            continue  # some acceptance set is never visited: not accepting
        counterexample = _counterexample(spec, gba, nodes, adj, parent,
                                         sorted(comp), comp_set)
        return {
            "status": "violated",
            "conclusion": ("an infinite execution from the initial position "
                           "violates the formula"),
            "formula": spec["formula"],
            "method": METHOD,
            "stats": stats,
            "counterexample": counterexample,
        }

    return {
        "status": "holds",
        "conclusion": ("every infinite execution from the initial position "
                       "satisfies the formula"),
        "formula": spec["formula"],
        "method": METHOD,
        "stats": stats,
        "counterexample": None,
    }


def _counterexample(spec, gba, nodes, adj, parent, comp, comp_set):
    """Build the lasso counterexample and per-step subformula evidence."""
    v0 = comp[0]

    # Prefix: BFS tree path from an initial product node to the loop.
    chain = []
    cur = v0
    while parent[cur] is not None:
        prev, tid = parent[cur]
        chain.append((prev, tid, cur))
        cur = prev
    chain.reverse()
    prefix_nodes = ([chain[0][0]] + [e[2] for e in chain]) if chain else [v0]

    # Loop: closed walk from v0 inside the SCC visiting every acceptance set.
    loop_edges = []
    cur = v0
    for acc in gba.accepting:
        target = min(u for u in comp if nodes[u][1] in acc)
        loop_edges.extend(_bfs_path(adj, comp_set, cur, target))
        cur = target
    loop_edges.extend(_bfs_path(adj, comp_set, cur, v0))
    if not loop_edges:
        # Degenerate case (no acceptance sets, or all targets equal v0):
        # any internal edge closes a cycle through v0.
        for u in comp:
            internal = [(v, tid) for v, tid in adj[u] if v in comp_set]
            if internal:
                v, tid = internal[0]
                loop_edges = (_bfs_path(adj, comp_set, v0, u)
                              + [(u, tid, v)]
                              + _bfs_path(adj, comp_set, v, v0))
                break
    if not loop_edges:
        raise RuntimeError("accepting SCC without a cycle (internal error)")

    loop_start_index = len(prefix_nodes) - 1
    trace_nodes = prefix_nodes + [e[2] for e in loop_edges[:-1]]
    trace_positions = [nodes[pid][0] for pid in trace_nodes]

    prefix_out = []
    for i, pid in enumerate(prefix_nodes):
        prefix_out.append({
            "position": nodes[pid][0],
            "via_transition": None if i == 0 else chain[i - 1][1],
        })
    loop_out = [{"transition": tid, "position": nodes[dst][0]}
                for _src, tid, dst in loop_edges]

    # Evidence: exact truth of every subformula on the infinite lasso.
    labels_seq = [spec["labels"][p] for p in trace_positions]
    subs = sorted(subformulas(spec["ast"]),
                  key=lambda g: (formula_size(g), to_str(g)))
    values = eval_lasso_many(subs, labels_seq, loop_start_index)
    if values[spec["ast"]][0]:
        raise RuntimeError("counterexample does not falsify the formula "
                           "(internal error)")
    evidence = []
    for i, pos in enumerate(trace_positions):
        evidence.append({
            "index": i,
            "phase": "prefix" if i < loop_start_index else "loop",
            "position": pos,
            "subformulas": {to_str(g): values[g][i] for g in subs},
        })

    return {
        "summary": ("prefix of %d step(s) followed by a loop of %d step(s) "
                    "repeated forever" % (loop_start_index, len(loop_edges))),
        "loop_start_index": loop_start_index,
        "loop_start_position": nodes[v0][0],
        "prefix": prefix_out,
        "loop": loop_out,
        "evidence": evidence,
    }
