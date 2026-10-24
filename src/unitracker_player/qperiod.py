"""Quasi-periodic subterms of phi: a node of phi alone with ``f(phi + Q) = f(phi) + D`` for every
``phi >= P0``, found by structure, then verified and minimised on its values; an expression whose
every phi is inside such subterms with ``D = 0`` is periodic in phi from the largest ``P0``."""

import ast
import math
from dataclasses import dataclass

from unitracker_grammar.expr import compile_expr

from unitracker_player.nodes import children as _children
from unitracker_player.nodes import op as _op

SEARCH = 1 << 16


@dataclass(frozen=True)
class Sig:
    """``f(phi + q) = f(phi) + d`` for ``phi >= p0``."""

    p0: int
    q: int
    d: int


class _Finder:
    """Signatures of the phi-only nodes of one expression; ``values(node, n)`` its first
    ``n`` values, evaluated with Python's semantics."""

    def __init__(self, namespace: dict):
        self.namespace = namespace
        self.sigs: dict = {}
        self.cache: dict = {}

    def values(self, node, n: int) -> list:
        """The node's values at phi in ``[0, n)``."""
        held = self.cache.get(id(node))
        if held is None or held[0] is not node:
            held = self.cache[id(node)] = (node, compile_expr(node, self.namespace, zero=True))
        return [held[1](0, phi, 0) for phi in range(n)]

    def sig(self, node) -> Sig | None:
        """The node's signature, or None where it reads v or N, or has none in reach."""
        if id(node) not in self.sigs:
            kids = [self.sig(c) for c in _children(node)]
            self.sigs[id(node)] = self._sig(node, _op(node), kids)
        return self.sigs[id(node)]

    def _settle(self, node, s: Sig, inside) -> int | None:
        """The first ``phi >= s.p0`` from which ``inside(f(phi))`` is false for good, for a node
        stepping ``s.d != 0`` per period."""
        if s.p0 + s.q > SEARCH:
            return None
        vals = self.values(node, s.p0 + s.q)[s.p0 :]
        k = 0
        for x in vals:
            j = 0
            while inside(x + j * s.d):
                j += 1
                if j * s.q > SEARCH:
                    return None
            k = max(k, j)
        return s.p0 + k * s.q

    def _sig(self, node, op, kids) -> Sig | None:
        # pylint: disable=too-many-return-statements,too-many-branches
        if op == "leaf":
            if isinstance(node, ast.Constant):
                return Sig(0, 1, 0)
            return Sig(0, 1, 1) if node.id == "phi" else None
        if any(k is None for k in kids):
            return None
        if op in ("+", "-"):
            a, b = kids
            q = math.lcm(a.q, b.q)
            d = a.d * (q // a.q) + (1 if op == "+" else -1) * b.d * (q // b.q)
            return Sig(max(a.p0, b.p0), q, d)
        if op == "neg":
            return Sig(kids[0].p0, kids[0].q, -kids[0].d)
        const = [c.value if isinstance(c, ast.Constant) else None for c in _children(node)]
        if op == "*":
            a, b = kids
            if const[0] is not None:
                return Sig(b.p0, b.q, const[0] * b.d)
            if const[1] is not None:
                return Sig(a.p0, a.q, const[1] * a.d)
            return _periodic(a, b)
        if op in ("//", "%") and const[1] is not None:
            a, c = kids[0], const[1]
            if c == 0:
                return Sig(0, 1, 0)
            q = a.q * abs(c)
            return Sig(a.p0, q, a.d * (1 if c > 0 else -1) if op == "//" else 0)
        if op in ("//", "%"):
            return _periodic(*kids)
        if op in ("abs", "min", "max") or op in ("==", "!=", "<", "<=", ">", ">="):
            return self._shape(node, op, kids)
        return self._lookup(node, op, kids)

    def _shape(self, node, op, kids) -> Sig | None:
        if len(kids) == 2:
            a, b = kids
            q = math.lcm(a.q, b.q)
            da, db = a.d * (q // a.q), b.d * (q // b.q)
            p0 = max(a.p0, b.p0)
            if da == db:
                return Sig(p0, q, da if op in ("min", "max") else 0)
            diff = ast.BinOp(
                node.args[0] if op in ("min", "max") else node.left,
                ast.Sub(),
                node.args[1] if op in ("min", "max") else node.comparators[0],
            )
            h = Sig(p0, q, da - db)
            pos = h.d > 0
            start = self._settle(diff, h, (lambda x: x <= 0) if pos else (lambda x: x >= 0))
            if start is None:
                return None
            if op in ("min", "max"):
                wins = b if (op == "min") == pos else a
                return Sig(start, q, db if wins is b else da)
            return Sig(start, 1, 0)
        a = kids[0]
        if a.d == 0:
            return a
        start = self._settle(node.args[0], a, (lambda x: x < 0) if a.d > 0 else (lambda x: x > 0))
        return None if start is None else Sig(start, a.q, abs(a.d))

    def _lookup(self, node, op, kids) -> Sig | None:
        a = kids[0]
        if a.d == 0:
            return a
        values = self.namespace["_F"] if op == "F" else None
        size = _size(values, self.namespace, node)
        if size is None:
            return None
        arg = node.args[0] if op == "F" else node.slice
        rising = a.d > 0
        low, high = size
        start = self._settle(arg, a, lambda x: x < high if rising else x >= low)
        return None if start is None else Sig(start, 1, 0)


def _size(ftable, namespace, node) -> tuple[int, int] | None:
    """``(low, high)``: the table is 0 below ``low`` and from ``high`` on."""
    if ftable is not None:
        low = high = 0
        while ftable(high):
            high += 1
        while ftable(low - 1):
            low -= 1
        return low, high
    table = namespace["_T"].get(node.value.id)
    return (0, len(table.args[0])) if table is not None else None


def _periodic(a: Sig, b: Sig) -> Sig | None:
    if a.d or b.d:
        return None
    return Sig(max(a.p0, b.p0), math.lcm(a.q, b.q), 0)


def _minimise(values: list, s: Sig) -> Sig:
    """The least period dividing ``s.q`` and the least start for it, on ``values`` of
    ``[0, s.p0 + 2 s.q)``."""
    best = s
    for q in sorted(d for d in range(1, s.q + 1) if s.q % d == 0):
        if (s.d * q) % s.q:
            continue
        d = s.d * q // s.q
        if all(values[x + q] == values[x] + d for x in range(s.p0, s.p0 + s.q)):
            best = Sig(s.p0, q, d)
            break
    p0 = best.p0
    while p0 and values[p0 - 1 + best.q] == values[p0 - 1] + best.d:
        p0 -= 1
    return Sig(p0, best.q, best.d)


def period(node, namespace: dict) -> tuple | None:
    """``(p0, q)``: ``node`` is the same function of v and N at phi and phi + q for every
    phi >= p0; None where that is not proved."""
    finder = _Finder(namespace)
    p0, q = 0, 1

    def walk(n) -> bool:
        nonlocal p0, q
        s = finder.sig(n)
        if s is not None and s.d == 0 and s.p0 + 2 * s.q <= SEARCH:
            values = finder.values(n, s.p0 + 2 * s.q)
            if all(values[x + s.q] == values[x] for x in range(s.p0, s.p0 + s.q)):
                s = _minimise(values, s)
                p0, q = max(p0, s.p0), math.lcm(q, s.q)
                return True
        if isinstance(n, ast.Name):
            return n.id != "phi"
        return all(walk(c) for c in _children(n))

    return (p0, q) if walk(node) else None
