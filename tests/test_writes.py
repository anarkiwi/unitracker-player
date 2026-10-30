"""A plain integer on any cell is a write: the cell takes it in its phase on the tick it applies,
and the slot it is written to is left unbound (docs/semantics.md, Cells)."""

import pytest
from song import cutoff, doc, freq, pulse, reg
from unitracker_grammar import frequency as F

from unitracker_player import play

AD, SR, CTRL = 5, 6, 4
ENV = "ctrl:[{ctrl:65,note:+0},end]"
HR = f"{{ad:9,sr:240,{ENV},restart:[{{gate:0,ad:15,sr:0}},{{}},end]}}"


def test_a_pulse_integer_leaves_a_binding_of_another_slot_modulating():
    """Slot b adds 10 a tick; the 100 written to slot a is fired once, b adds 10 to it a tick
    after tick."""
    head = "tempo:2\nslots:{pw:{a:{},b:{}}}\n"
    text = doc("[C4.i1 b.pw:'v + 10',- pw:100,-]", head=head)
    assert pulse(play(text)) == [10, 20, 110, 120, 130, 140]


def test_a_pulse_integer_replaces_a_binding_of_its_own_slot():
    text = doc("[C4.i1 pw:'v + 10',- pw:100,-]", head="tempo:2\n")
    assert pulse(play(text)) == [10, 20, 100, 100, 100, 100]


@pytest.mark.parametrize(
    "phrase, ad, sr, gate",
    [
        ("[C4.i1,D.i1]", [9, 9, 15, 9, 9, 9], [240, 240, 0, 240, 240, 240], [1, 1, 0, 1, 1, 1]),
        ("[C4.i1,D.i1 ad:5]", [9, 9, 5, 5, 5, 5], [240, 240, 0, 240, 240, 240], [1, 1, 0, 1, 1, 1]),
    ],
)
def test_an_ad_integer_in_a_restart_row_is_written_before_the_note(phrase, ad, sr, gate):
    """D falls on tick 3, its restart's one lookahead row on tick 2: ad 15 and sr 0 with the gate
    off, then the instrument's ad 9 and sr 240 on the note, or the event's ad 5 after them."""
    frames = play(doc(phrase, HR, head="tempo:3\n"), 6)
    assert reg(frames, AD) == ad
    assert reg(frames, SR) == sr
    assert [r & 1 for r in reg(frames, CTRL)] == gate


def test_the_envelope_rows_of_a_restart_write_through_a_slot_of_the_cell():
    """Slot b of ad adds 1 a tick on what slot a was written."""
    head = "tempo:3\nslots:{ad:{a:{},b:{mod:'v + 1'}}}\n"
    frames = play(doc("[C4.i1,D.i1]", HR, head=head), 6)
    assert reg(frames, AD) == [10, 11, 16, 10, 11, 12]


@pytest.mark.parametrize(
    "command, expected",
    [
        ("freq:+3", [F(48) + 3] + [F(48)] * 3),
        ("freq:-3", [F(48) - 3] + [F(48)] * 3),
        ("freq:'F(N) + 3'", [F(48) + 3] * 4),
    ],
)
def test_a_frequency_integer_is_the_note_plus_n_on_one_tick(command, expected):
    assert freq(play(doc(f"[C4.i1 {command},-]", head="tempo:2\n"))) == expected


@pytest.mark.parametrize(
    "slots, expected",
    [
        ("", [100] * 3 + [500] * 5),
        ("slots:{cut:{a:{},b:{mod:'v + 1'}}}\n", [101, 102, 103, 501, 502, 503, 504, 505]),
    ],
)
def test_a_filter_row_s_cut_integer_is_written_once_in_its_row_s_tick(slots, expected):
    ins = f"{{ad:9,sr:240,{ENV},filter:[{{hold:3,cut:100}},{{cut:500}},end]}}"
    assert cutoff(play(doc("[C4.i1,-]", ins, head=f"tempo:4\n{slots}"))) == expected
