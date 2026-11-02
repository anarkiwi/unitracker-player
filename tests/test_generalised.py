"""Tempo lists, the output term of a cell and expressions that read cells by name, each worked
out by hand from docs/semantics.md and the grammar's docs/format-2.5.md."""

import pytest
from song import cutoff, doc, freq, pulse, reg, timeline
from unitracker_grammar import CanonicalError, frequency as F

from unitracker_player import load, play
from unitracker_player.compiler import compile_document
from unitracker_player.document import read as read_document
from unitracker_player.native import Unsupported, export
from unitracker_player.render import render

NOTES = [48, 50, 52, 53, 55, 57]
CTRL = "ctrl:[{ctrl:65,note:+0},end]"
SWING = "ins:\n i1:{ad:9,sr:240," + CTRL + "}\n"


def voices(one: str, two: str, head: str = "tempo:[1,2,3]\n") -> str:
    """Two voices each playing six notes once, the second's third carrying ``two``."""
    return (
        f"unitracker:2.5\nmeta:{{title:T}}\nvoices:2\n{head}{SWING}phrase:\n"
        f" p1:[C4.i1{one},D,E,F,G,A]\n p2:[C4.i1,D,E{two},F,G,A]\n"
        "song:\n - voice:[p1|end]\n - voice:[p2|end]\n"
    )


def sounding(frames, voice: int, starts: list, end: int) -> list:
    """The notes ``voice`` plays from ``starts`` to tick ``end``, as frequency words."""
    return freq(frames, voice)[:end] == [F(k) for k in timeline(starts, NOTES, end)]


@pytest.mark.parametrize(
    "two, first, second",
    [
        (" tempo:3/1/2", [0, 2, 7, 10, 11, 13], [0, 1, 3, 6, 7, 9]),
        (" vtempo:3/1/2", [0, 2, 7, 8, 10, 15], [0, 1, 3, 6, 7, 9]),
    ],
    ids=["tempo", "vtempo"],
)
def test_a_tempo_list_cycles_and_tempo_restarts_every_voice_where_vtempo_its_own(
    two, first, second
):
    """The header's [1,2,3] gives the second voice steps of 1, 2, 3, 1, 2, 3 ticks; its third
    note restarts the list [3,1,2] at tick 3. The first voice's steps are [2,5,1] from its own
    vtempo: its second note began at tick 2 and ends at 7 by that list's 5. Then `tempo` restarts
    [3,1,2] at its entry 0 (E 3, F 1, G 2, A 3); `vtempo` leaves [2,5,1] running (E 1, F 2, G 5)."""
    frames = play(voices(" vtempo:2/5/1", two), 16)
    assert sounding(frames, 1, first, 16)
    assert sounding(frames, 2, second, 12)


def test_a_looping_orderlist_period_counts_a_tempo_list_s_length():
    """Two steps a loop of [1,2,3]: 3, 4 and 5 ticks, then the list is back at its start."""
    text = (
        f"unitracker:2.5\nmeta:{{title:T}}\nvoices:1\ntempo:[1,2,3]\n{SWING}"
        "phrase:\n p1:[C4.i1,-]\nsong:\n - voice:[p1|loop:0]\n"
    )
    assert compile_document(read_document(text)).periods == ((0, 0, 12),)
    frames = play(text, 36)
    assert (frames[:12] == frames[12:24]).all() and (frames[:12] == frames[24:]).all()
    assert freq(frames)[:6] == [F(48), F(48), F(48), F(48), F(48), F(48)]


def test_a_tempo_of_zero_is_refused_anywhere_in_the_list():
    with pytest.raises(Exception, match="tempo of 0"):
        load(doc(head="tempo:[2,3,0]\n"))
    with pytest.raises(Exception, match="tempo of 0"):
        load(doc("[C4.i1 vtempo:2/0/3,-]"))


# ---- terms ----------------------------------------------------------------------------------


def ins(fields: str, ctrl: str = CTRL) -> str:
    """An instrument with the control program ``ctrl`` and the pulse, filter and frequency
    ``fields``."""
    return "{ad:9,sr:240," + ctrl + "," + fields + "}"


def termed(fields: str, phrase: str = "[C4.i1,-*7]", ticks: int = 6, tempo: int = 1):
    """The frames of ``phrase`` played by an instrument with ``fields``."""
    return play(doc(phrase, ins=ins(fields), head=f"tempo:{tempo}\n"), ticks)


@pytest.mark.parametrize(
    "term, expected",
    [
        ("N", [49, 50, 51, 52, 53, 54]),
        ("v + 1", [2, 4, 6, 8, 10, 12]),
        ("100", [101, 102, 103, 104, 105, 106]),
    ],
)
def test_a_freq_term_is_added_to_the_cell_and_never_fed_back(term, expected):
    """The slot `v + 1` counts 1, 2, 3, ...; the term N (48) adds 48 each tick without
    accumulating, `v + 1` its own count (so twice the cell's), an integer holds."""
    assert freq(termed(f"freq:v + 1,mask.freq:{term}")) == expected


def test_a_term_wraps_to_the_register():
    frames = termed("freq:v + 1,mask.freq:65535", ticks=3)
    assert freq(frames) == [0, 1, 2]


def test_none_ends_a_term_s_binding_and_the_term_holds_its_value():
    frames = termed("freq:v + 1,mask.freq:v + 10", "[C4.i1,D mask.freq:none,-*7]", 6, 2)
    assert freq(frames) == [11, 22, 23, 24, 25, 26]


@pytest.mark.parametrize(
    "fields, expected",
    [
        ("pw:v + 1,mask.pw:4095", [0, 1, 2, 3]),
        ("pw:v + 1,mask.pw:v + 3", [4, 8, 12, 16]),
    ],
)
def test_a_pw_term_wraps_to_twelve_bits(fields, expected):
    assert pulse(termed(fields, ticks=4)) == expected


def test_a_cut_term_is_added_to_the_song_s_cutoff():
    frames = termed("filter:v + 2,mask.filter:v + 1", "[C4.i1,-*7]", 5, 2)
    assert cutoff(frames) == [3, 6, 9, 12, 15]


def test_ctrl_s_term_is_xored_and_its_mask_integer_is_unchanged():
    def played(ctrl: str, ticks: int) -> list:
        return reg(
            play(doc("[C4.i1,-*7]", ins=f"{{ad:9,sr:240,{ctrl}}}", head="tempo:1\n"), ticks), 4
        )

    assert played("ctrl:[{ctrl:65,mask:8,note:+0},end]", 2) == [73, 73]
    assert played("ctrl:[{ctrl:65,mask.ctrl:v ^ 8,note:+0},end]", 4) == [73, 65, 73, 65]
    stopped = "ctrl:[{ctrl:65,mask.ctrl:v ^ 8,note:+0},{mask.ctrl:none},end]"
    assert played(stopped, 3) == [73, 73, 73]


def test_a_document_without_a_term_plays_as_before():
    assert freq(termed("freq:v + 1", ticks=3)) == [1, 2, 3]


# ---- reads ----------------------------------------------------------------------------------

PW_ORDERS = {
    "freq first": "",
    "pw first": "order:[events,ctrl,pulse,filter,pw,freq,cut,read]\n",
}


@pytest.mark.parametrize(
    "order, expected",
    [("freq first", [0, 10, 20, 30]), ("pw first", [10, 20, 30, 40])],
)
def test_an_expression_reads_the_value_another_cell_holds_in_the_phase_order(order, expected):
    """pw counts 10, 20, ...; freq is pw: in the default order freq's phase runs before pw's, so
    it reads the previous tick's value (the cell's 0 at tick 0), the order line moving pw first
    makes it this tick's."""
    frames = play(
        doc("[C4.i1,-*7]", ins=ins("pw:v + 10,freq:pw"), head="tempo:1\n" + PW_ORDERS[order]), 4
    )
    assert freq(frames) == expected


def test_an_expression_reads_a_song_cell_and_its_own_cell_as_the_tick_began():
    counting = "ctrl:[{ctrl:65,note:+0,vol:v + 1},end]"
    text = doc("[C4.i1,-*7]", ins=ins("freq:vol", counting), head="tempo:1\n")
    assert freq(play(text, 4)) == [0, 1, 2, 3]
    head = "tempo:1\nslots:{freq:{wave:{},b:{}}}\n"
    twice = ins("freq:v + 5,b.freq:freq + 1000")
    assert freq(play(doc("[C4.i1,-*7]", ins=twice, head=head), 3)) == [1000, 2000, 3000]


def test_a_cell_s_name_is_no_template_parameter():
    with pytest.raises(CanonicalError):
        load(doc(mods={"m(ad)": "v + ad"}))


# ---- the native machine plays none of them --------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        voices("", " vtempo:3/1/2"),
        doc("[C4.i1,-*7]", ins=ins("mask.freq:5")),
        doc("[C4.i1,-*7]", ins=ins("freq:pw")),
    ],
    ids=["tempo", "term", "read"],
)
def test_the_native_machine_declines_what_it_has_no_form_for(text):
    with pytest.raises(Unsupported):
        export(load(text))


def test_a_looping_song_with_a_term_and_a_read_renders_to_its_proved_loop():
    fields = "pw:phi % 4,mask.freq:N,freq:pw + 5"
    text = doc("[C4.i1,-*3]", ins=ins(fields), ending=0, head="tempo:1\n")
    rendered = render(read_document(text))
    assert rendered.loop is not None
    frames = play(text, len(rendered.frames) + 20)
    assert (frames[: len(rendered.frames)] == rendered.frames).all()
    tail = rendered.frames[rendered.loop :]
    span = len(tail)
    assert (frames[rendered.loop + span : rendered.loop + 2 * span] == tail).all()
