"""An expression's proved period in φ holds on its values."""

import random

import pytest
from unitracker_grammar import PAL_HZ
from unitracker_grammar.expr import compile_expr, indexed, tree

from unitracker_player.expr import note_table
from unitracker_player.qperiod import period

TABLES = {"G": (0, 744, 0, 835, 884, 993, 0, 1114)}
NAMESPACE = {"_F": note_table(PAL_HZ), "_T": {k: indexed(v) for k, v in TABLES.items()}}


def _holds(text: str, found, rng: random.Random) -> bool:
    fn = compile_expr(tree(text), NAMESPACE, zero=True)
    p0, q = found
    for _ in range(300):
        v, n, phi = rng.randrange(1 << 16), rng.randrange(-5, 100), p0 + rng.randrange(5000)
        if fn(v, phi, n) != fn(v, phi + q * rng.randrange(1, 9), n):
            return False
    return True


@pytest.mark.parametrize(
    "text, expected",
    [
        ("16*abs((φ - (φ >= 11) - (φ >= 15) - 32) % 128 - 64) + 512", (15, 128)),
        ("(-(φ % 3 == 0) + 1)*(128*((φ - (φ + 3) // 3 - 2) % 10 >= 5) - 64) + v", (0, 15)),
        ("((v - F(N)) % 65536)*(φ >= 1) + 139*(φ == 11) + F(N) - 139", (12, 1)),
        ("(v - G[N]) % 65536 + G[N]", (0, 1)),
        ("v + min(3, φ) + max(0, 4 - φ) + F(φ % 7) + G[φ % 5 - 1]", (4, 35)),
        ("v + G[φ - 3] + F(120 - φ)", (121, 1)),
        ("v + φ", None),
        ("v * (φ // 4)", None),
    ],
)
def test_period(text, expected):
    found = period(tree(text), NAMESPACE)
    assert found == expected
    if found is not None:
        assert _holds(text, found, random.Random(text))


def _random(rng: random.Random, depth: int) -> str:
    if depth == 0 or rng.random() < 0.3:
        return rng.choice(["v", "φ", "N", str(rng.randint(-40, 40))])
    a, b = _random(rng, depth - 1), _random(rng, depth - 1)
    form = rng.choice(["+", "-", "*", "//", "%", "cmp", "abs", "min", "max", "T", "F"])
    if form == "cmp":
        return f"({a} {rng.choice(['==', '<', '>='])} {b})"
    if form in ("abs",):
        return f"abs({a})"
    if form in ("min", "max"):
        return f"{form}({a}, {b})"
    if form == "T":
        return f"G[{a}]"
    if form == "F":
        return f"F(min(max({a}, -50), 200))"
    return f"({a}) {form} ({b})"


def test_every_proved_period_holds():
    rng = random.Random(3)
    proved = 0
    for _ in range(1500):
        text = _random(rng, 4)
        found = period(tree(text), NAMESPACE)
        if found is not None:
            proved += 1
            assert _holds(text, found, rng), text
    assert proved > 300
