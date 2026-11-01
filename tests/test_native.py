"""The native call machine against the Python one: random modulator expressions, the checked-in
songs, the writes of a call, the code modulators' machine, and the paths back to Python."""

import random

import numpy as np
import pytest
from conftest import FIXTURES, Agreeing, lifted
from song import I1, doc
from test_code_play import ADD, GATE, VIB, block

from unitracker_player import Player, PlayError, load, native, play
from unitracker_player import player as player_module
from unitracker_player.code import Routine
from unitracker_player.native import Unsupported, export, postfix

pytestmark = pytest.mark.skipif(not native.enabled(), reason="the native core is not built")

LEAVES = ["v", "phi", "N", "n"]
UNARY = ["-({})", "~({})", "!({})", "abs({})", "lo({})", "hi({})", "u8({})", "u16({})", "sx({})"]
BINARY = ["+", "-", "*", "//", "%", "|", "^", "&", "==", "!=", "<", "<=", ">", ">=", "&&", "||"]


def expression(rng: random.Random, depth: int) -> str:
    """A random expression of the modulator grammar, every operand parenthesised."""
    if depth == 0 or rng.random() < 0.15:
        return rng.choice(LEAVES) if rng.random() < 0.6 else str(rng.randint(-300, 300))
    kind = rng.random()

    def sub() -> str:
        return expression(rng, depth - 1)

    if kind < 0.5:
        return f"({sub()} {rng.choice(BINARY)} {sub()})"
    if kind < 0.65:
        return rng.choice(UNARY).format(sub())
    if kind < 0.75:
        return f"{rng.choice(['min', 'max'])}({sub()},{sub()})"
    if kind < 0.82:
        return f"F({sub()})"
    if kind < 0.9:
        return f"ite({sub()},{sub()},{sub()})"
    return f"({sub()} {rng.choice(['<<', '>>'])} {rng.randint(0, 12)})"


def played(text: str, ticks: int) -> np.ndarray:
    """The grid of ``ticks`` frames by the native player, which the autouse fixture has agree
    with the Python one."""
    return play(text, ticks)


def parsed(rng: random.Random, cell: str) -> str:
    """A random document binding ``cell`` to a random expression the grammar accepts."""
    for _ in range(200):
        body = expression(rng, 3)
        phrase = f"[C4.i1 {cell}:'{body}',-*7,D.i1 {cell}:'{body}',-*3]"
        try:
            text = doc(phrase=phrase, ins=I1)
            load(text)
        except ValueError:
            continue
        return text
    raise AssertionError("no random expression parsed")


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("cell", ["freq", "pw", "ad", "cut", "res"])
def test_random_expressions_play_as_they_do_in_python(cell, seed):
    rng = random.Random(f"{cell}{seed}")
    for _ in range(4):
        assert played(parsed(rng, cell), 40).shape == (40, 25)


@pytest.mark.parametrize("name", FIXTURES)
def test_the_checked_in_songs_play_as_they_do_in_python(name):
    song = load(lifted(name))
    assert native.player(song) is not None
    assert len(play(song, 1500)) == 1500


def test_the_writes_of_each_call_are_the_python_player_s():
    """The native segments of a frame hold the (sr, ad, ctrl) written by each call."""
    text = doc("[C4.i1,-,D env:_._.64/15.1.65,-,-,E env:_._.64/_.7.65,-]", I1, head="tempo:1\n")
    song = load(text)
    ours, theirs = Player(song), native.player(song)
    frames = 0
    while (regs := ours.step()) is not None:
        assert bytes(regs) == theirs.step()
        assert [[tuple(s) for s in segs] for segs in ours.segments] == theirs.segments()
        frames += 1
    assert frames == 7 and theirs.step() is None


# pylint: disable-next=too-many-locals
def test_a_code_modulator_runs_as_its_python_routine_does():
    ins = f"{{ad:9,sr:240,{GATE},pw:add(3000),freq:vib(3,40,6)}}"
    mods = {"add(d)": block(ADD), "vib(c,s,w)": block(VIB)}
    text = doc(ins=ins, head="state:{freq:{t:1}}\n", mods=mods)
    stream, image = export(load(text))
    machine = native._core.NativePlayer(stream, image)  # pylint: disable=protected-access
    writer = native._Export(load(text))  # pylint: disable=protected-access
    writer.stream()
    routines = [f for f in writer.fns if isinstance(f, Routine)]
    assert routines
    rng = random.Random(7)
    for routine in routines:
        for _ in range(200):
            v, phi, note, event = rng.randrange(65536), rng.randrange(400), rng.randrange(-8, 90), 5
            state = bytearray(rng.randrange(256) for _ in range(1))
            kept = bytes(state)
            want = routine(v, phi, note, event, state)
            got, left = machine.fire(writer.routine_ids[id(routine)], v, phi, note, event, kept)
            assert (got, left) == (want, bytes(state))


def test_a_code_modulator_over_its_budget_is_the_python_player_s_error():
    spin = "loop:\njmp loop\nrts"
    ins = f"{{ad:9,sr:240,{GATE},pw:spin()}}"
    text = doc(ins=ins, mods={"spin()": block(spin)})
    with pytest.raises(PlayError, match="4096"):
        play(text, 4)


def test_a_song_the_native_core_refuses_plays_in_python(monkeypatch):
    song = load(doc())
    monkeypatch.setattr(
        native, "export", lambda s: (_ for _ in ()).throw(Unsupported("a construct"))
    )
    assert native.player(song) is None
    assert play(song, 6).shape == (6, 25)


def test_the_environment_switches_the_native_core_off(monkeypatch):
    monkeypatch.setenv("UNITRACKER_PLAYER_NATIVE", "0")
    assert not native.enabled() and native.player(load(doc())) is None
    assert play(doc(), 3).shape == (3, 25)


def test_a_constant_past_62_bits_has_no_native_form():
    with pytest.raises(Unsupported, match="62 bits"):
        postfix(native.ast.Constant(1 << 70), {})


def test_a_stream_that_is_malformed_is_refused():
    stream, image = export(load(doc()))
    core = native._core  # pylint: disable=protected-access
    for broken in ([], [1, 2, 3], stream[:-1], [*stream, 0], [0, *stream[1:]]):
        with pytest.raises(core.NativeError):
            core.NativePlayer(broken, image)


def test_a_difference_between_the_players_is_an_assertion():
    class Wrong:
        """A native player that gets every register wrong."""

        @staticmethod
        def run(frames):
            return bytes([1]) * 25 * frames

    with pytest.raises(AssertionError, match="frame 0 register 0"):
        Agreeing(load(doc()), Wrong()).run(3)


def test_a_song_that_ends_returns_the_frames_it_played():
    grid = play(doc(), None)
    assert len(grid) == 24 and not native.player(load(doc())).looping
    assert native.player(load(doc(rows=("[p1]",), ending=0))).looping
    assert player_module.play_python(load(doc())).tolist() == grid.tolist()
