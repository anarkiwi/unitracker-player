"""Holds: a slot held on its hold events passes its cell's value through and its φ waits
(docs/semantics.md, Slots and Modulators)."""

import ast

import pytest
from song import cutoff, doc, freq, pulse
from unitracker_grammar import frequency as F

from unitracker_player import Player, load, play

SWEEP = "{ctrl: [{ctrl: 65}, end], pw: 10*φ + 1}"


def holding(cell: str, events: str) -> str:
    """The slots line holding ``cell``'s one slot on ``events``."""
    return f"slots: {{{cell}: {{{cell}: {{hold: [{events}]}}}}}}\n"


def sweep(held: set, ticks: int, start: int = 0) -> list:
    """``10*φ + 1`` from ``start``, φ advancing on every tick but ``held``, 0 before; a held
    tick before the first firing keeps the pulse width."""
    out, phi, last = [], 0, 0
    for t in range(ticks):
        if t >= start:
            if t in held:
                out.append(last)
                continue
            last = 10 * phi + 1
            phi += 1
        out.append(last)
    return out


@pytest.mark.parametrize(
    "hold, held",
    [
        ("step", {0, 3, 6, 9}),
        ("step-1", {2, 5, 8}),
        ("step-2", {1, 4, 7}),
        ("last", {9}),
        ("last-1", {8}),
        ("note", {0, 9}),
        ("note-1", {8}),
        ("legato", {3}),
    ],
)
def test_a_pulse_holds_on_its_events(hold, held):
    text = doc(
        phrase="[C4.i1, D4!legato, -.i1!pw, E4!pw]",
        ins=SWEEP,
        head="tempo: 3\n" + holding("pw", hold),
    )
    assert pulse(play(text, 12)) == sweep(held, 12)


def test_a_legato_hold_is_the_tick_its_note_applies():
    """D4 !legato applies on its restart's first-tick row, a tick before its step, and holds
    there."""
    ins = "{ctrl: [{ctrl: 65}, end], pw: 10*φ + 1, restart: [{hold: 1, gate: 0}, {ctrl: 9}, end]}"
    text = doc(
        phrase="[C4.i1, D4!legato, -*2]", ins=ins, head="tempo: 3\n" + holding("pw", "legato")
    )
    assert pulse(play(text, 12)) == sweep({2}, 12)


def test_a_modulator_held_on_the_tick_it_starts_fires_first_on_its_next():
    """A note starts the instrument's pulse on a held step: the step keeps the pulse width, and
    the pulse fires first on the next tick."""
    text = doc(phrase="[C4.i1, D4]", ins=SWEEP, head="tempo: 3\n" + holding("pw", "step"))
    assert pulse(play(text, 6)) == [0, 1, 11, 11, 1, 11]


def test_a_read_is_the_step_less_its_instrument_s_restart_lookahead():
    """The voice reads its step its instrument's lookahead earlier, the instrument it plays before
    the step names another; a voice with no instrument reads nothing."""
    ins = {
        "i1": "{ctrl: [{ctrl: 65}, end], pw: 10*φ + 1, restart: [{hold: 2, gate: 0}, {}, end]}",
        "i2": "{ctrl: [{ctrl: 65}, end], restart: [{hold: 1, gate: 0}, {}, end]}",
    }
    text = doc(
        phrase="[-, C4.i1, -, -.i2!pw, -!pw]", ins=ins, head="tempo: 4\n" + holding("pw", "read")
    )
    held = {6, 10, 15}
    assert pulse(play(text, 20)) == sweep(held, 20, start=4)


def test_a_control_program_row_holds_on_any_key_but_ctrl_and_gate():
    """A row setting a note is a row event; one setting only ctrl and gate is not, nor is a
    restart's row. The held tick passes the frequency through, at the row's old note."""
    ins = (
        "{ctrl: [{ctrl: 65}, {gate: 1}, {note: +12}, {ctrl: 64}, end], freq: F(N) + φ,"
        " restart: [{hold: 1, sr: 1}, {}, end]}"
    )
    text = doc(phrase="[C4.i1, -*2]", ins=ins, head="tempo: 3\n" + holding("freq", "row"))
    c4, c5 = F(48), F(60)
    assert freq(play(text, 6)) == [c4, c4 + 1, c4 + 1, c5 + 2, c5 + 3, c5 + 4]


@pytest.mark.parametrize(
    "expr, expected",
    [
        ("F(N) + φ", [F(48), F(48) + 1, F(48) + 1, F(50) + 2]),
        ("v + 1 + (φ < 1)*F(N)", [F(48) + 1, F(48) + 2, F(48) + 2, F(48) + 3]),
    ],
)
def test_a_held_frequency_keeps_its_word_through_a_legato_note(expr, expected):
    """On a held legato tick the slot passes the word through, a closed form of N too; it
    takes the new note at its next firing."""
    ins = f"{{ctrl: [{{ctrl: 65}}, end], freq: {expr}}}"
    text = doc(phrase="[C4.i1, D4!legato]", ins=ins, head="tempo: 2\n" + holding("freq", "legato"))
    assert freq(play(text, 4)) == expected


def test_the_cutoff_holds_on_the_events_of_the_voice_that_set_it():
    """Voice 2 sets the cutoff; voice 1's steps do not hold it, voice 2's do."""
    head = "voices: 2\ntempo: 2\n" + holding("cut", "step")
    text = doc(
        phrase={"p1": "[C4.i1!vtempo:3*3]", "p2": "[C4.i2, -, -*2]"},
        ins={"i1": "{ctrl: [{ctrl: 65}, end]}", "i2": "{ctrl: [{ctrl: 65}, end], filter: 8*φ + 8}"},
        rows=("[p1, p2]",),
        head=head,
    )
    assert cutoff(play(text, 8)) == [0, 8, 8, 16, 16, 24, 24, 32]


def test_no_hold_events_hold_nothing():
    text = doc(phrase="[C4.i1, D4!legato, -]", ins=SWEEP, head="tempo: 3\n")
    assert pulse(play(text, 9)) == sweep(set(), 9)


def test_a_compiled_expression_carries_its_node():
    song = load(doc(ins="{ctrl: [{ctrl: 65}, end], pw: 10*φ + 1}"))
    player = Player(song)
    player.step()
    assert ast.unparse(player.voices[0].pw_mods[0][0].node) == "10 * φ + 1"
