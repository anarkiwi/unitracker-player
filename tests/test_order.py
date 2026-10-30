"""The order line: the phases of a tick in the order they run (docs/semantics.md)."""

import pytest
from song import doc, freq, pulse, reg
from unitracker_grammar import frequency as F

from unitracker_player import PlayError, play

RESTART = "{ctrl:[{ctrl:65,sr:99},{loop:0}],pw:10*phi + 1,restart:[{gate:0,ad:15,sr:0},{},end]}"
ORDERS = {
    "default": "events,ctrl,pulse,filter,freq,pw,cut,read",
    "modulators first": "events,freq,pw,cut,ctrl,pulse,filter,read",
    "programs first": "ctrl,pulse,filter,events,freq,pw,cut,read",
    "read after ctrl": "events,ctrl,read,pulse,filter,freq,pw,cut",
}


def ordered(order: str, head: str = "", **kwargs) -> str:
    """A document whose order line is ``order``."""
    return doc(head=f"{head}order:[{order}]\n", **kwargs)


@pytest.mark.parametrize(
    "order, sr",
    [
        (None, [99] * 6),
        ("default", [99] * 6),
        ("read after ctrl", [99, 99, 0, 99, 99, 99]),
    ],
)
def test_a_read_phase_after_the_control_phase_overwrites_the_control_row_on_the_read_tick(
    order, sr
):
    """The restart's first row falls on the read tick. Run after the control phase and before
    the sr phase, its sr 0 replaces the control program's 99 and shows on that tick; run after
    the sr phase it waits for the next tick's, where the control row's 99 replaces it; without an
    order line it runs with the events."""
    kwargs = {"phrase": "[C4.i1,D.i1]", "ins": RESTART}
    text = (
        doc(head="tempo:3\n", **kwargs)
        if order is None
        else ordered(ORDERS[order], "tempo:3\n", **kwargs)
    )
    frames = play(text, 6)
    assert reg(frames, 6) == sr
    assert [r & 1 for r in reg(frames, 4)] == [1, 1, 0, 1, 1, 1]


@pytest.mark.parametrize(
    "order, expected",
    [("default", [F(48), F(60), F(60)]), ("modulators first", [F(48), F(48), F(60)])],
)
def test_a_frequency_before_the_control_phase_sees_the_last_tick_s_row_note(order, expected):
    text = ordered(ORDERS[order], ins="{ctrl:[{ctrl:65},{note:+12},end],freq:F(N)}")
    assert freq(play(text, 3)) == expected


@pytest.mark.parametrize(
    "order, pw, ctrl",
    [
        ("default", [1, 11, 21], [65, 65, 65]),
        ("modulators first", [0, 1, 11], [0, 65, 65]),
        ("programs first", [0, 1, 11], [0, 65, 65]),
    ],
)
def test_a_binding_or_program_started_after_its_phase_applies_from_the_next_tick(order, pw, ctrl):
    """A control row binding the pulse width after the pw phase fires from the next tick; a
    note starting its control program after the ctrl phase runs its first row on the next. A
    ctrl written after the ctl phase shows from the next tick."""
    frames = play(ordered(ORDERS[order], ins="{ctrl:[{ctrl:65,pw:10*phi + 1},end]}"), 3)
    assert pulse(frames) == pw and reg(frames, 4) == ctrl


@pytest.mark.parametrize(
    "order",
    [
        "events,ctrl,pulse,filter,freq,pw,cut",
        "events,ctrl,pulse,filter,freq,pw,cut,read,read",
        "events,ctrl,ctrl,filter,freq,pw,cut,read",
    ],
)
def test_order_refusals(order):
    """Every phase exactly once."""
    with pytest.raises(PlayError, match="order"):
        play(ordered(order), 1)
