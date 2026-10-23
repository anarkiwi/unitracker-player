"""Format 1.22: modulator slots fired in their declared order, templates, instances and their
bindings (docs/semantics.md)."""

import numpy as np
import pytest
from song import cutoff, doc, freq, pulse
from unitracker_grammar import frequency as F

from unitracker_player import PlayError, play, read
from unitracker_player.render import render

CTRL = "{ctrl: [{ctrl: 65}, end]}"


def v22(text: str) -> str:
    """``text`` as a format 1.22 document."""
    return text.replace("unitracker: 1.21", "unitracker: 1.22", 1)


@pytest.mark.parametrize(
    "order, expected", [("a: {}, b: {}", [20, 60, 140, 300]), ("b: {}, a: {}", [10, 30, 70, 150])]
)
def test_slots_fire_in_their_declared_order_each_on_the_last_s_value(order, expected):
    text = doc(ins="{a.pw: v + 10, b.pw: 2*v}", head=f"slots: {{pw: {{{order}}}}}\n")
    assert pulse(play(v22(text), 4)) == expected


def test_a_held_slot_passes_its_value_through_and_keeps_its_phi():
    """b holds on each step: a's firing passes through it, and b's φ waits."""
    text = doc(
        ins="{pw: v + 1, b.pw: v + 100*φ}",
        head="tempo: 3\nslots: {pw: {a: {}, b: {hold: [step]}}}\n",
    )
    assert pulse(play(v22(text), 7)) == [1, 2, 103, 104, 305, 606, 607]


@pytest.mark.parametrize(
    "reset, expected",
    [("step", [1, 11, 21, 1, 11, 21, 1]), ("step-1", [1, 11, 1, 11, 21, 1, 11])],
)
def test_a_slot_s_phi_restarts_on_its_reset_events(reset, expected):
    text = doc(ins="{pw: 10*φ + 1}", head=f"tempo: 3\nslots: {{pw: {{a: {{reset: [{reset}]}}}}}}\n")
    assert pulse(play(v22(text), 7)) == expected


@pytest.mark.parametrize(
    "event, expected", [("row.note", [1, 11, 1, 11, 21]), ("row", [1, 1, 1, 1, 11])]
)
def test_a_row_event_names_rows_that_set_any_key_but_ctrl_and_gate_or_its_key(event, expected):
    ins = "{ctrl: [{ctrl: 65}, {ad: 5}, {note: +0}, {ad: 6}, end], pw: 10*φ + 1}"
    text = doc(ins=ins, head=f"slots: {{pw: {{a: {{reset: [{event}]}}}}}}\n")
    assert pulse(play(v22(text), 5)) == expected


def test_row_freq_is_a_row_binding_any_slot_of_freq():
    ins = "{ctrl: [{ctrl: 65}, {tick.freq: v}, end], freq: 10*φ + 1}"
    head = "slots: {freq: {wave: {reset: [row.freq]}, tick: {}}}\n"
    assert freq(play(v22(doc(ins=ins, head=head)), 4)) == [1, 1, 11, 21]


def test_stops_end_one_slot_or_hold_the_cell():
    """``none`` ends one slot; ``hold`` ends every slot and the pulse width holds."""
    text = doc(
        phrase="[C4.i1, -!b.pw:none, -!pw:hold, -]",
        ins="{pw: v + 1, b.pw: v + 10}",
        head="tempo: 2\nslots: {pw: {a: {}, b: {}}}\n",
    )
    assert pulse(play(v22(text), 8)) == [11, 22, 23, 24, 24, 24, 24, 24]


def test_a_frequency_with_no_binding_is_f_n_until_a_hold():
    text = doc(
        phrase="[C4.i1, -!tick.freq:none, D4!legato, -!freq:hold, E4!legato]",
        ins="{ctrl: [{ctrl: 65}, end], tick.freq: v + 1}",
        head="tempo: 2\nslots: {freq: {wave: {}, tick: {}}}\n",
    )
    c4, d4 = F(48), F(50)
    assert freq(play(v22(text), 10)) == [1, 2, c4, c4, d4, d4, d4, d4, d4, d4]


def test_an_instrument_s_none_ends_its_slot_and_its_other_slots_stay():
    text = doc(
        phrase="[C4.i1, D4.i2]",
        ins={"i1": "{freq: F(N), tick.freq: v + 1}", "i2": "{tick.freq: none}"},
        head="tempo: 2\nslots: {freq: {wave: {}, tick: {}}}\n",
    )
    assert freq(play(v22(text), 4)) == [F(48) + 1, F(48) + 1, F(50), F(50)]


def test_n_is_the_event_note_on_every_cell():
    """``n`` is the event note with its transpose, not a row's note, on freq, pw and cut."""
    text = doc(
        ins="{ctrl: [{ctrl: 65, note: +12}, end], pw: n*10, filter: n*4, freq: F(n) + N}",
        rows=("[p1+2]",),
    )
    frames = play(v22(text), 3)
    assert freq(frames) == [F(50) + 62] * 3
    assert pulse(frames) == [500] * 3 and cutoff(frames) == [200] * 3


def test_templates_bind_their_parameters_by_instance_or_inline():
    text = doc(
        ins="{pw: x2, freq: porta {s: 7}}",
        mod={"porta": "v + s", "ramp": "s*φ + o"},
        x={"x1": "porta {s: 5}", "x2": "ramp {o: 100, s: 3}"},
    )
    frames = play(v22(text), 3)
    assert pulse(frames) == [100, 103, 106] and freq(frames) == [7, 14, 21]


@pytest.mark.parametrize(
    "x, match",
    [
        ("v {s: 1}", "binds a template"),
        ("porta", "missing"),
        ("porta {s: 1, t: 2}", "unknown"),
        ("porta {s: a}", "integer"),
        ("v + q", "unbound parameter"),
    ],
)
def test_a_template_s_parameters_are_its_bag_s_integers(x, match):
    text = doc(phrase="[C4.i1!pw:x1]", ins=CTRL, mod={"porta": "v + s"}, x={"x1": x})
    with pytest.raises(PlayError, match=match):
        play(v22(text), 1)


REBIND = doc(
    phrase="[C4.i1!pw:x1, -!pw:x1, -!pw:x3, -!pw:x2]",
    ins=CTRL,
    x={"x1": "10*φ + 1", "x2": "20*φ + 1", "x3": "10*φ + 1"},
    head="tempo: 2\n",
)


def test_binding_the_bound_modulator_keeps_its_phi_and_another_restarts_it():
    assert pulse(play(v22(REBIND), 8)) == [1, 11, 21, 31, 41, 51, 1, 21]


def test_a_1_21_command_restarts_its_modulator_and_owns_its_cell():
    """The same events in format 1.21: each command starts φ at 0, and owns the cell, where
    format 1.22 chains a command's slot after the instrument's."""
    assert pulse(play(REBIND, 8)) == [1, 11, 1, 11, 1, 11, 1, 21]
    owned = doc(phrase="[C4.i1!pw:x1]", ins="{pw: v + 10}", x={"x1": "v + 1"})
    assert pulse(play(owned, 3)) == [1, 2, 3]
    chained = owned.replace("!pw:x1", "!b.pw:x1").replace(
        "song:", "slots: {pw: {a: {}, b: {}}}\nsong:"
    )
    assert pulse(play(v22(chained), 3)) == [11, 22, 33]


@pytest.mark.parametrize(
    "phrase, ins, expected",
    [
        ("[C4.i1, D4.i2]", "{b.pw: v + 2*φ}", [0, 101, 200, 302]),
        ("[C4.i1, -!b.pw:x1, D4.i2, -]", "{ad: 1}", [0, 101, 207, 307, 407, 507]),
    ],
)
def test_a_note_rebinds_its_instrument_s_slots_alone(phrase, ins, expected):
    """A note binds the slots its instrument names; every other binding, a command's too, and
    its φ run on."""
    text = doc(
        phrase=phrase,
        ins={"i1": "{pw: 100*φ, b.pw: v + φ}", "i2": ins},
        x={"x1": "v + 7"},
        head="tempo: 2\nslots: {pw: {a: {}, b: {}}}\n",
    )
    assert pulse(play(v22(text), len(expected))) == expected


def test_a_slot_s_default_binding_runs_from_tick_0():
    """Each voice's slot, and the cutoff's by voice 1, starts bound to its default; a note
    whose instrument does not name it leaves it."""
    text = doc(
        phrase={"p1": "[C4.i1, -]", "p2": "[-, C4.i1]"},
        ins="{ad: 1}",
        rows=("[p1, p2]",),
        mod={"up": "v + 3"},
        x={"x1": "v + 1"},
        head="voices: 2\ntempo: 2\nslots: {freq: {wave: {mod: up}}, cut: {c: {mod: x1}}}\n",
    )
    frames = play(v22(text), 4)
    assert freq(frames, 1) == freq(frames, 2) == [3, 6, 9, 12]
    assert cutoff(frames) == [1, 2, 3, 4]


@pytest.mark.parametrize(
    "version, head, ins, match",
    [
        ("1.22", "hold: {pw: [step]}\n", CTRL, "slots line"),
        ("1.21", "slots: {pw: {a: {}}}\n", CTRL, "slots"),
        ("1.21", "", "{tick.freq: v}", "slot prefix"),
        ("1.22", "slots: {freq: {wave: {}}}\n", "{tick.freq: v}", "no slot tick"),
        ("1.22", "slots: {pw: {a: {}, a: {}}}\n", CTRL, "twice"),
        ("1.22", "slots: {pw: {a: {hold: [row.wait]}}}\n", CTRL, "no row key"),
        ("1.22", "slots: {pw: {a: {mod: nothing}}}\n", CTRL, "unknown modulator"),
    ],
)
def test_slot_refusals(version, head, ins, match):
    text = doc(ins=ins, head=head).replace("1.21", version, 1)
    with pytest.raises(PlayError, match=match):
        play(text, 1)


def test_a_looping_1_22_song_repeats_from_its_loop():
    """The loop proof reduces each slot's φ by its modulator's period."""
    text = doc(
        phrase="[C4.i1, -!tick.freq:x1, D4.i1, -]",
        ins="{ctrl: [{ctrl: 65, note: +0}, end], freq: F(N), tick.freq: none}",
        x={"x1": "v + 8*(φ % 6 < 3) - 4"},
        head="tempo: 3\nslots: {freq: {wave: {reset: [row.note]}, tick: {hold: [step-1]}}}\n",
        ending=0,
    )
    rendered = render(read(v22(text)))
    assert rendered.loop is not None
    period = len(rendered.frames) - rendered.loop
    ticks = len(rendered.frames) + 3 * period
    index = [
        t if t < len(rendered.frames) else rendered.loop + (t - rendered.loop) % period
        for t in range(ticks)
    ]
    assert np.array_equal(play(v22(text), ticks), rendered.frames[index])


@pytest.mark.parametrize(
    "ins, cell",
    [("{pw: x1}", pulse), ("{ctrl: [{ctrl: 65}, end], freq: x1}", freq)],
)
def test_a_note_s_instrument_binding_restarts_phi_on_the_bound_modulator(ins, cell):
    """Two notes of one instrument: the second restarts the walk its instrument binds, where a
    command binding the same instance runs on."""
    text = doc(
        phrase="[C4.i1, C4.i1, -!pw:x1, -]", ins=ins, x={"x1": "10*φ + 1"}, head="tempo: 2\n"
    )
    walk = [1, 11, 1, 11, 21, 31, 41, 51]
    assert cell(play(v22(text), 8)) == walk
