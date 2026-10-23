"""Delta runs rebuild each cell, and the grammar plays back every stream's runs."""

import random

import numpy as np
import pytest

from unitracker_player import streams as S


@pytest.mark.parametrize("bits", [8, 16])
def test_runs_rebuild_the_values_split_at_cuts_and_opcode_limits(bits):
    rng = np.random.default_rng(bits)
    values = np.repeat(rng.integers(0, 1 << bits, 40), rng.integers(1, 300, 40))
    cuts = [len(values) // 3, len(values) // 2]
    runs = S.runs(values, bits, cuts)
    deltas = np.concatenate([np.full(n, d) for d, n in runs])
    assert np.array_equal(np.cumsum(deltas) % (1 << bits), values)
    assert all(n <= S.MAX_RUN[S.width(d)] for d, n in runs)
    assert all(-(1 << (bits - 1)) <= d < 1 << (bits - 1) for d, _ in runs)
    starts = np.cumsum([0] + [n for _, n in runs])
    assert set(cuts) <= set(starts.tolist())


def test_the_cutoff_cell_is_its_11_bit_value():
    frames = np.zeros((2, 25), dtype=np.uint8)
    frames[1, 21], frames[1, 22] = 5, 0xAB
    assert S.cell_values(frames, (21, 22), "cut").tolist() == [0, 0xAB << 3 | 5]


def _sequences(seed: int) -> list:
    rng = random.Random(seed)
    motifs = [
        [(rng.randint(-3, 3) * 300, rng.randint(1, 4)) for _ in range(rng.randint(2, 9))]
        for _ in range(6)
    ]
    seqs = []
    for _ in range(5):
        seq = []
        for _ in range(rng.randint(5, 40)):
            seq += rng.choice(motifs) if rng.random() < 0.8 else [(rng.randint(-9, 9), 1)]
        loop = rng.randrange(len(seq))
        seqs.append(seq[:loop] + [S.LOOP] + seq[loop:] + [S.END])
    return seqs


@pytest.mark.parametrize("seed", range(8))
def test_the_grammar_plays_every_stream_and_is_smaller(seed):
    seqs = _sequences(seed)
    grammar = S.inline(S.repair(seqs))
    assert [S.expand(grammar, s) for s in grammar.starts] == seqs
    assert all(isinstance(x, (int, tuple)) for rule in grammar.rules for x in rule)
    raw = sum(S.symbol_bytes(x) for s in seqs for x in s)
    assert S.size(grammar) < raw
    memo: dict = {}
    assert max(S.depth(grammar, s, memo) for s in grammar.starts) >= 1


def test_a_rule_kept_is_cheaper_called_than_copied():
    seqs = [[(1, 1), (2, 1)] * 10 + [S.END]]
    grammar = S.inline(S.repair(seqs))
    assert S.expand(grammar, grammar.starts[0]) == seqs[0]
    assert S.size(grammar) < S.size(S.Grammar(seqs, []))
