"""Format 1.22's order line: the phases of a tick in the order they run (docs/semantics.md)."""

import numpy as np
import pytest
from song import doc, freq, pulse, reg
from unitracker_grammar import frequency as F

from unitracker_player import PlayError, play

READ_LAST = "order: [events, ctrl, pulse, filter, freq, pw, cut, read]\n"
RESTART = (
    "{ctrl: [{ctrl: 65, sr: 99}, {loop: 0}], pw: 10*φ + 1,"
    " restart: [{gate: 0, ad: 15, sr: 0}, {}, end]}"
)


def v22(text: str) -> str:
    return text.replace("unitracker: 1.21", "unitracker: 1.22", 1)


def ordered(text: str, order: str) -> str:
    """Format 1.22 ``text`` with the order line ``order``."""
    return v22(text).replace("song:", f"order: [{order}]\nsong:", 1)


def test_without_an_order_line_a_tick_runs_the_default_order():
    """A 1.22 document without the line plays as its 1.21 self, the read row with the events."""
    text = doc(phrase="[C4.i1, D4.i1, -]", ins=RESTART, head="tempo: 3\n")
    assert np.array_equal(play(v22(text)), play(text))


@pytest.mark.parametrize("order, sr", [(None, [99] * 6), ("read", [99, 99, 0, 99, 99, 99])])
def test_a_read_phase_last_overwrites_the_control_row_on_the_read_tick(order, sr):
    """The restart's first row falls on the read tick; run last, its sr wins over the control
    program's."""
    text = doc(phrase="[C4.i1, D4.i1]", ins=RESTART, head="tempo: 3\n")
    text = v22(text) if order is None else v22(text).replace("song:", READ_LAST + "song:", 1)
    frames = play(text, 6)
    assert reg(frames, 6) == sr
    assert [r & 1 for r in reg(frames, 4)] == [1, 1, 0, 1, 1, 1]


ORDERS = {
    "default": "events, ctrl, pulse, filter, freq, pw, cut, read",
    "modulators first": "events, freq, pw, cut, ctrl, pulse, filter, read",
    "programs first": "ctrl, pulse, filter, events, freq, pw, cut, read",
}


@pytest.mark.parametrize(
    "order, expected",
    [("default", [F(48), F(60), F(60)]), ("modulators first", [F(48), F(48), F(60)])],
)
def test_a_frequency_before_the_control_phase_sees_the_last_tick_s_row_note(order, expected):
    text = doc(ins="{ctrl: [{ctrl: 65}, {note: +12}, end], freq: F(N)}")
    assert freq(play(ordered(text, ORDERS[order]), 3)) == expected


@pytest.mark.parametrize(
    "order, pw, ctrl",
    [
        ("default", [1, 11, 21], [65, 65, 65]),
        ("modulators first", [0, 1, 11], [65, 65, 65]),
        ("programs first", [0, 1, 11], [0, 65, 65]),
    ],
)
def test_a_binding_or_program_started_after_its_phase_applies_from_the_next_tick(order, pw, ctrl):
    """A control row binding the pulse width after the pw phase fires from the next tick; a
    note starting its control program after the ctrl phase runs its first row on the next."""
    text = doc(ins="{ctrl: [{ctrl: 65, pw: 10*φ + 1}, end]}")
    frames = play(ordered(text, ORDERS[order]), 3)
    assert pulse(frames) == pw and reg(frames, 4) == ctrl


@pytest.mark.parametrize(
    "version, order",
    [
        ("1.22", "events, ctrl, pulse, filter, freq, pw, cut"),
        ("1.22", "events, ctrl, pulse, filter, freq, pw, cut, read, read"),
        ("1.22", "events, ctrl, ctrl, filter, freq, pw, cut, read"),
        ("1.21", ORDERS["default"]),
    ],
)
def test_order_refusals(version, order):
    """Every phase exactly once, and in format 1.22 alone."""
    text = doc().replace("song:", f"order: [{order}]\nsong:", 1).replace("1.21", version, 1)
    with pytest.raises(PlayError, match="order"):
        play(text, 1)
