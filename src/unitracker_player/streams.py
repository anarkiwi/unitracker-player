"""The rendered registers as cell streams of delta runs, compressed to one grammar.

A cell's stream is its runs ``(d, n)``: add ``d`` on each of ``n`` ticks. All streams are
compressed together by Re-Pair; then each rule cheaper inlined than called is inlined.
"""

import heapq
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

# (name, registers, bits); the cutoff plays its 11-bit value: registers cut & 7, cut >> 3.
CELLS = tuple(
    (f"{name}{v + 1}", tuple(7 * v + r for r in regs), bits)
    for v in range(3)
    for name, regs, bits in (
        ("freq", (0, 1), 16),
        ("pw", (2, 3), 16),
        ("ctrl", (4,), 8),
        ("ad", (5,), 8),
        ("sr", (6,), 8),
    )
) + (("cut", (21, 22), 16), ("resfilt", (23,), 8), ("modevol", (24,), 8))
MAX_RUN = {0: 127, 8: 63, 16: 31}
CALL_BYTES, RET_BYTES, JUMP_BYTES = 3, 1, 3
LOOP, END = "loop", "end"


def cell_values(frames: np.ndarray, regs: tuple, name: str) -> np.ndarray:
    """A cell's value per tick."""
    f = frames.astype(np.int64)
    if name == "cut":
        return f[:, 21] & 7 | f[:, 22] << 3
    if len(regs) == 2:
        return f[:, regs[0]] | f[:, regs[1]] << 8
    return f[:, regs[0]]


def runs(values: np.ndarray, bits: int, cuts=()) -> list:
    """The runs ``(d, n)`` taking a cell from 0 through ``values``, ``d`` signed to ``bits``,
    split at the ticks ``cuts`` and at each opcode's longest run."""
    mod, half = 1 << bits, 1 << (bits - 1)
    deltas = (np.diff(values, prepend=0) + half) % mod - half
    out, t = [], 0
    for end in sorted(set(cuts) | {len(values)}):
        while t < end:
            d, n = int(deltas[t]), 1
            while t + n < end and deltas[t + n] == d:
                n += 1
            t += n
            longest = MAX_RUN[width(d)]
            while n:
                out.append((d, min(n, longest)))
                n -= min(n, longest)
    return out


def width(d: int) -> int:
    """The bits a delta's run encodes it in."""
    return 0 if d == 0 else 8 if -128 <= d < 128 else 16


def symbol_bytes(x) -> int:
    """The encoded size of a symbol: a run, a call, or a marker."""
    if isinstance(x, int):
        return CALL_BYTES
    if x == LOOP:
        return 0
    if x == END:
        return JUMP_BYTES
    return 1 + width(x[0]) // 8


@dataclass
class Grammar:
    """``starts`` each stream's top-level symbols, ``rules`` each rule's body; a symbol is a
    run ``(d, n)``, ``LOOP``, ``END`` or a rule's index."""

    starts: list
    rules: list


def repair(seqs: list) -> Grammar:
    """Re-Pair over ``seqs``: the most frequent pair of adjacent symbols becomes a rule,
    repeatedly, while one occurs twice; markers never pair."""
    # pylint: disable=too-many-locals
    sym, nxt, prv, heads = [], [], [], []
    for seq in seqs:
        heads.append(len(sym) if seq else -1)
        for i, x in enumerate(seq):
            sym.append(("m", x, len(sym)) if isinstance(x, str) else ("t", x))
            prv.append(len(sym) - 2 if i else -1)
            nxt.append(-1)
            if i:
                nxt[-2] = len(sym) - 1
    occ, heap = defaultdict(set), []

    def link(i, on: bool):
        if i >= 0 and nxt[i] >= 0:
            pair = (sym[i], sym[nxt[i]])
            if on:
                occ[pair].add(i)
                heapq.heappush(heap, (-len(occ[pair]), pair))
            else:
                occ[pair].discard(i)

    for i in range(len(sym)):
        link(i, True)
    rules = []
    while heap:
        count, pair = heapq.heappop(heap)
        live = occ.get(pair)
        if not live or -count != len(live) or len(live) < 2:
            continue
        rule = ("r", len(rules))
        rules.append(pair)
        for i in sorted(live):
            j = nxt[i]
            if j < 0 or (sym[i], sym[j]) != pair:
                continue
            link(prv[i], False)
            link(i, False)
            link(j, False)
            sym[i], nxt[i] = rule, nxt[j]
            if nxt[j] >= 0:
                prv[nxt[j]] = i
            link(prv[i], True)
            link(i, True)
        occ.pop(pair, None)

    def decode(x):
        return x[1]

    starts = []
    for i in heads:
        out = []
        while i >= 0:
            out.append(decode(sym[i]))
            i = nxt[i]
        starts.append(out)
    return Grammar(starts, [[decode(a), decode(b)] for a, b in rules])


def inline(grammar: Grammar) -> Grammar:
    """The grammar with each rule whose calls cost more bytes than its copies inlined (Re-Pair
    numbers a rule after those it calls), the rest renumbered from 0."""
    rules, uses = grammar.rules, defaultdict(int)
    for seq in grammar.starts + rules:
        for x in seq:
            if isinstance(x, int):
                uses[x] += 1
    keep, body, memo = set(), {}, {}
    for r, rule in enumerate(rules):
        body[r] = sum(
            body[x] if isinstance(x, int) and x not in keep else symbol_bytes(x) for x in rule
        )
        if uses[r] * CALL_BYTES + body[r] + RET_BYTES < uses[r] * body[r]:
            keep.add(r)
    numbering = {r: i for i, r in enumerate(sorted(keep))}

    def flat(seq):
        out = []
        for x in seq:
            if isinstance(x, int) and x not in keep:
                if x not in memo:
                    memo[x] = flat(rules[x])
                out += memo[x]
            else:
                out.append(numbering[x] if isinstance(x, int) else x)
        return out

    return Grammar([flat(s) for s in grammar.starts], [flat(rules[r]) for r in sorted(keep)])


def depth(grammar: Grammar, seq: list, memo: dict) -> int:
    """The deepest nesting of calls from ``seq``."""
    out = 0
    for x in seq:
        if isinstance(x, int):
            if x not in memo:
                memo[x] = 1 + depth(grammar, grammar.rules[x], memo)
            out = max(out, memo[x])
    return out


def size(grammar: Grammar) -> int:
    """The encoded bytes of every stream and rule, with each rule's return."""
    total = sum(symbol_bytes(x) for s in grammar.starts for x in s)
    return total + sum(sum(symbol_bytes(x) for x in r) + RET_BYTES for r in grammar.rules)


def expand(grammar: Grammar, seq: list) -> list:
    """The runs and markers ``seq`` plays."""
    out = []
    for x in seq:
        out += expand(grammar, grammar.rules[x]) if isinstance(x, int) else [x]
    return out
