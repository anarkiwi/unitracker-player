"""Format 2.5 as docs/format-2.5.md specifies it: each construct on a small document whose
register grid is worked out by hand from the specification."""

import numpy as np
import pytest
from song import cutoff, doc, freq, pulse, reg
from unitracker_grammar import CanonicalError

from unitracker_player import Player, PlayError, load, play, read
from unitracker_player.compiler import _phases
from unitracker_player.render import render

ENV = 5
SR = 6
D417 = 0x17
D418 = 0x18
BARE = "{ctrl:[{ctrl:65,note:+0},end]}"
NOTE_ONLY = "{sr:240}"


def run(commands: str, ticks: int, ins: str = NOTE_ONLY, head: str = "tempo:4\n", **kw):
    """The frames of a one-voice song whose first note carries ``commands``."""
    return play(doc(f"[C4.i1{commands},-*3]", ins=ins, head=head, **kw), ticks)


def high(frames, address: int) -> list:
    """Register ``address``' bits 4-7."""
    return [x >> 4 for x in reg(frames, address)]


# ---- standard notes -------------------------------------------------------------------------


@pytest.mark.parametrize("note, word", [("A4", 7493), ("C3", 2228)])
def test_a_note_is_the_word_rounded_to_the_nearest_at_the_clock(note, word):
    """440 * 2**((k - 57)/12) * 2**24 / 985248 is 7492.50 for A4 and 2227.53 for C3."""
    frames = play(doc(f"[{note}.i1,-]", ins=BARE), 1)
    assert freq(frames) == [word]


def test_rounding_is_no_longer_a_clock_key():
    text = doc("[C4.i1,-]", ins=BARE, head="clock:{cycles_per_tick:19656,clock_hz:985248}\n")
    with pytest.raises(CanonicalError):
        load(text.replace("clock_hz:985248}", "clock_hz:985248,rounding:floor}"))


# ---- calls ----------------------------------------------------------------------------------

COUNT = "{pw:v + 1}"


@pytest.mark.parametrize(
    "calls, lag, expected",
    [
        ("calls:{frame:3}\n", "", [3, 6, 9, 12, 15]),
        ("calls:{frame:3}\n", "lag:1\n", [2, 5, 8, 11, 14]),
        ("calls:{frame:3}\n", "lag:4\n", [0, 2, 5, 8, 11]),
    ],
)
def test_a_frame_samples_the_registers_after_its_last_call(calls, lag, expected):
    """pw is the number of calls run; 16 calls make 5 frames of 3, the 16th call a partial frame
    that is not output; lag k shows what the cells made k calls before the frame's last call."""
    frames = run("", 99, COUNT, f"tempo:4\n{lag}{calls}")
    assert pulse(frames) == expected


C4, D4 = 4455, 5001


@pytest.mark.parametrize(
    "calls, expected",
    [
        ("calls:{frame:2,full:[1,0]}\n", [C4, C4, D4, D4]),
        ("calls:{frame:2,full:[1,0,0]}\n", [C4, C4, C4, D4, D4, D4]),
        ("calls:{frame:5,full:[1,0,0]}\n", [C4, D4]),
    ],
)
def test_the_sequencer_counts_full_calls_only(calls, expected):
    """Tempo 2 puts the second note at sequencer tick 2, the song's end at 4, which are calls 4
    and 8 (pattern 1,0), 6 and 12 (1,0,0); the calls before the end make whole frames only."""
    text = doc("[C4.i1,D.i1]", ins=BARE, head=f"tempo:2\n{calls}")
    assert freq(play(text)) == expected


def test_init_is_the_first_full_call():
    """With the pattern 0,1 call 0 is reduced: nothing is played on it, the init note on call 1."""
    text = doc("[-*2]", ins=BARE, head="tempo:2\ncalls:{frame:1,full:[0,1]}\ninit:[C4.i1]\n")
    assert freq(play(text)) == [0] + [C4] * 8


def test_a_programs_wait_and_hold_count_calls():
    """Row 0 at call 0, row 1 at 0 + hold 1 + wait 2 = call 3, though 3 calls are 1.5 full ones."""
    ctrl = "{ctrl:[{ctrl:65,note:+0},{wait:2,ctrl:64},end]}"
    text = doc("[C4.i1,-]", ins=ctrl, head="tempo:2\ncalls:{frame:1,full:[1,0]}\n")
    assert reg(play(text), 4) == [65, 65, 65] + [64] * 5


def test_a_slots_k_counts_calls_and_its_step_the_full_calls():
    """Steps are calls 0, 4, 8, 12; step-1 is the call before each, held, the cell counting the
    other calls."""
    head = "tempo:2\nslots:{pw:{a:{hold:[step-1]}}}\ncalls:{frame:1,full:[1,0]}\n"
    frames = run("", 99, COUNT, head)
    assert pulse(frames) == [1, 2, 3, 3, 4, 5, 6, 6, 7, 8, 9, 9, 10, 11, 12, 13]


RESTART = "{ad:9,sr:240,ctrl:[{ctrl:65,note:+0},end],restart:hr(2,9,3)}"


def test_read_and_events_do_not_run_on_reduced_calls():
    """D is at sequencer tick 6, call 12, its restart's first row at tick 4, call 8: ad 9 and sr 3
    and the gate off, once; the counter of ad fires on every call, 10 on a call the row or a
    note sets ad 9 and one more on each call after."""
    head = "tempo:3\nslots:{ad:{a:{},b:{mod:'v + 1'}}}\ncalls:{frame:1,full:[1,0]}\n"
    text = doc("[C4.i1,-,D.i1]", ins=RESTART, head=head)
    frames = play(text)
    assert reg(frames, ENV) == [*range(10, 18), 10, 11, 12, 13, *range(10, 16)]
    assert reg(frames, SR) == [240] * 8 + [3] * 4 + [240] * 6
    assert reg(frames, 4) == [65] * 8 + [64] * 4 + [65] * 6


# ---- the new cells --------------------------------------------------------------------------


def test_a_plain_integer_wraps_to_the_cell_s_mask():
    """ad 300 is 44, sr 511 255, ctrl 321 (control byte 65 with the gate on), res 20 is 4, route
    21 is 5, mode keeps bits 4-7 and vol 17 is 1."""
    frames = run(" ad:300 sr:511 ctrl:321 res:20 route:21 mode:255 vol:17", 1)
    assert frames[0][4:7].tolist() == [65, 44, 255]
    assert frames[0][D417:].tolist() == [0x45, 0xF1]


@pytest.mark.parametrize("mode, d418", [(128, 0x8F), (16, 0x1F), (15, 0x0F), (240, 0xFF)])
def test_mode_is_the_high_nibble_of_d418_with_voice_3_off_at_bit_7(mode, d418):
    assert reg(run(f" mode:{mode} vol:15", 1), D418) == [d418]


def test_a_program_row_sets_each_new_cell():
    ins = "{ctrl:[{ctrl:65,note:+0,ad:1,sr:2,res:3,route:4,mode:16,vol:5},end]}"
    frames = play(doc("[C4.i1,-]", ins=ins), 1)
    assert frames[0][4:7].tolist() == [65, 1, 2]
    assert frames[0][D417:].tolist() == [3 << 4 | 4, 16 | 5]


def test_route_on_and_route_off_set_and_clear_bits_of_the_route_cell():
    """Voice 1's bit is 1: route 8, then 8 + (1 | 2), then without (1 | 8): 2."""
    ins = "{ctrl:[{ctrl:65,note:+0,route:8},{route_on:2},{route_off:8},end]}"
    frames = play(doc("[C4.i1,-*3]", ins=ins, head="tempo:2\n"), 3)
    assert [x & 15 for x in reg(frames, D417)] == [8, 11, 2]


def test_a_program_template_stands_for_integers_of_the_new_cells():
    ctrl = {"arp(a,b)": "[{ctrl:65,note:+0,ad:a,sr:b,res:a,route:b,mode:a,vol:b},end]"}
    text = doc("[C4.i1,-]", ins="{ctrl:arp(21,3)}", ctrl=ctrl)
    frames = play(text, 1)
    assert frames[0][5:7].tolist() == [21, 3]
    assert frames[0][D417:].tolist() == [5 << 4 | 3, 16 | 3]


def test_expressions_drive_ad_ctrl_and_vol_and_wrap_to_their_width():
    """From 0, ad adds 200 mod 256, ctrl adds 65 mod 256 (the gate is on, so the byte shows as
    it is), vol adds 5 mod 16."""
    frames = run(" ad:'v + 200' ctrl:'v + 65' vol:'v + 5'", 5, "{sr:240}")
    assert reg(frames, ENV) == [200, 144, 88, 32, 232]
    assert reg(frames, 4) == [65, 130, 195, 4, 69]
    assert reg(frames, D418) == [5, 10, 15, 4, 9]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("(phi << 2) ^ 5", [5, 1, 13, 9, 21]),
        ("(100 >> phi) | 1", [101, 51, 25, 13, 7]),
        ("~phi & 255", [255, 254, 253, 252, 251]),
        ("sx(phi + 126)", [126, 127, 3968, 3969, 3970]),
        ("4*(!phi)", [4, 0, 0, 0, 0]),
        ("(phi >= 2) && (phi < 4)", [0, 0, 1, 1, 0]),
        ("(phi == 0) || (phi == 3)", [1, 0, 0, 1, 0]),
        ("phi >= 2", [0, 0, 1, 1, 1]),
        ("!phi > 2", [1, 1, 1, 0, 0]),
        ("1 << (phi - 2)", [0, 0, 1, 2, 4]),
        ("hi(phi*300)", [0, 1, 2, 3, 4]),
        ("lo(phi*100)", [0, 100, 200, 44, 144]),
        ("u16(phi - 1)", [4095, 0, 1, 2, 3]),
        ("ite(phi % 2,7,9)", [9, 7, 9, 7, 9]),
        ("17 // phi", [0, 17, 8, 5, 4]),
        ("17 % phi", [0, 0, 1, 2, 1]),
    ],
)
def test_the_operators_and_functions_of_the_expression_language(text, expected):
    """Expressions compile with zero: a divisor or shift count that is 0 or negative gives 0."""
    assert pulse(run(f" pw:'{text}'", 5)) == expected


def test_a_plain_integer_sets_the_cell_and_leaves_its_bindings():
    """Slot b of ad counts, a 9 written to slot a is fired once and slot b fires on it, hold ends
    the binding."""
    head = "tempo:2\nslots:{ad:{a:{},b:{}}}\n"
    text = doc("[C4.i1 b.ad:'v + 1',- ad:9,- ad:hold,-]", ins=NOTE_ONLY, head=head)
    assert reg(play(text, 8), ENV) == [1, 2, 10, 11, 11, 11, 11, 11]


def test_a_plain_integer_ends_the_binding_of_the_slot_it_is_written_to():
    """The counter of slot a is replaced by the write, which fires once and leaves the cell 9."""
    text = doc("[C4.i1 ad:'v + 1',- ad:9,-*3]", ins=NOTE_ONLY, head="tempo:2\n")
    assert reg(play(text, 8), ENV) == [1, 2, 9, 9, 9, 9, 9, 9]


def test_slots_of_a_song_cell_fire_in_order_and_none_ends_one():
    head = "tempo:2\nslots:{vol:{a:{},b:{}}}\n"
    commands = " a.vol:'v + 1' b.vol:'v + 2'"
    text = doc(f"[C4.i1{commands},- b.vol:none,-]", ins=NOTE_ONLY, head=head)
    assert reg(play(text, 6), D418) == [3, 6, 7, 8, 9, 10]


def test_a_once_binding_fires_once_and_reset_restarts_phi():
    once = doc("[C4.i1 vol:'v + 3'!,-*3]", ins=NOTE_ONLY, head="tempo:2\n")
    assert reg(play(once, 4), D418) == [3, 3, 3, 3]
    again = doc("[C4.i1 vol:'phi + 1',- vol:reset,-]", ins=NOTE_ONLY, head="tempo:2\n")
    assert reg(play(again, 5), D418) == [1, 2, 1, 2, 3]


def test_an_instrument_binds_ad_and_a_plain_ad_writes_once_and_ends_its_slot():
    """Each note restarts phi; an ad of 9 replaces the binding of its slot and holds; none ends."""
    ins = {
        "i1": "{ad:phi*2 + 1,sr:240}",
        "i2": "{ad:9,sr:240}",
        "i3": "{ad:none,sr:240}",
        "i4": "{ad:v + 2,sr:240}",
    }
    head = "tempo:2\n"
    again = doc("[C4.i1,-,C4.i1,-]", ins=ins, head=head)
    assert reg(play(again, 6), ENV) == [1, 3, 5, 7, 1, 3]
    plain = doc("[C4.i4,-,C4.i2,-]", ins=ins, head=head)
    assert reg(play(plain, 6), ENV) == [2, 4, 6, 8, 9, 9]
    ended = doc("[C4.i4,-,C4.i3,-]", ins=ins, head=head)
    assert reg(play(ended, 6), ENV) == [2, 4, 6, 8, 8, 8]


# ---- code modulators and their state ----------------------------------------------------------

COUNTER = "inc {}\nlda {}\nsta v\nrts"


def block(src: str) -> str:
    return "|\n" + "\n".join("\t" + line for line in src.splitlines())


def test_state_is_a_voice_s_copy_for_a_voice_cell_and_the_song_s_for_a_song_cell():
    """Both voices count ad from their own t; vol, one cell of the song, counts on across the
    voices' bindings."""
    mods = {"cnt()": block(COUNTER.format("t", "t")), "cnu()": block(COUNTER.format("u", "u"))}
    phrases = {
        "p1": "[C4.i1 ad:cnt() vol:cnu(),-,-]",
        "p2": "[-,C4.i1 ad:cnt() vol:cnu(),-]",
    }
    head = "voices:2\ntempo:2\nstate:{ad:{t:1},vol:{u:1}}\n"
    text = doc(phrases, ins=NOTE_ONLY, head=head, rows=("[p1,p2]",), mods=mods)
    frames = play(text)
    assert reg(frames, ENV) == [1, 2, 3, 4, 5, 6]
    assert reg(frames, ENV + 7) == [0, 0, 1, 2, 3, 4]
    assert reg(frames, D418) == [1, 2, 3, 4, 5, 6]


# ---- order ----------------------------------------------------------------------------------

ROW_AD = "{ctrl:[{ctrl:65,note:+0,ad:9},end]}"


def ordered(order: str, ins: str, cell: str, head: str = "") -> str:
    slots = f"slots:{{{cell}:{{a:{{}},b:{{mod:'v + 1'}}}}}}\n"
    return doc("[C4.i1,-*2]", ins=ins, head=f"tempo:2\n{slots}{order}{head}")


def test_a_cell_phase_runs_after_the_programs_by_default():
    """The row writes ad 9 to slot a, the phase of ad fires it and slot b adds 1: 10, 11, 12."""
    assert reg(play(ordered("", ROW_AD, "ad"), 3), ENV) == [10, 11, 12]


def test_an_order_line_puts_a_cell_s_phase_before_the_programs():
    """Slot b adds 1 to 0 first, the row's write waits for the next tick's phase: 1, 10, 11."""
    order = "order:[events,ad,ctrl,pulse,filter,freq,pw,cut,read]\n"
    assert reg(play(ordered(order, ROW_AD, "ad"), 3), ENV) == [1, 10, 11]


def test_the_phases_an_order_line_leaves_out_follow_the_nearest_earlier_of_the_sequence():
    """ad is named before ctrl, so sr, res, route, mode and vol follow it, and ctl after cut."""
    order = ("events", "ad", "ctrl", "pulse", "filter", "freq", "pw", "cut", "read")
    assert _phases(order) == (
        *("events", "ad", "sr", "res", "route", "mode", "vol", "ctrl", "pulse"),
        *("filter", "freq", "pw", "cut", "ctl", "read"),
    )
    assert _phases(("events", "ctrl", "pulse", "filter", "freq", "pw", "cut", "read")) == (
        *("events", "ctrl", "pulse", "filter", "freq", "pw", "cut"),
        *("ctl", "ad", "sr", "res", "route", "mode", "vol", "read"),
    )


def test_ctl_is_the_phase_of_the_ctrl_cell():
    """Before the program writes ctrl 64 the cell's slot b adds 1 to 0 and the write waits for the
    next tick's phase; by default the row writes 64 and the phase adds 1."""
    row = "{ctrl:[{ctrl:64,note:+0},end]}"
    slots = "slots:{ctrl:{a:{},b:{mod:'v + 1'}}}\n"
    default = doc("[C4.i1,-*2]", ins=row, head=f"tempo:2\n{slots}")
    early = "order:[events,ctl,ctrl,pulse,filter,freq,pw,cut,read]\n"
    first = doc("[C4.i1,-*2]", ins=row, head=f"tempo:2\n{slots}{early}")
    assert reg(play(default, 3), 4) == [65, 66, 67]
    assert reg(play(first, 3), 4) == [1, 65, 66]


@pytest.mark.parametrize(
    "order",
    [
        "events,ctrl,pulse,filter,freq,pw,cut",
        "events,ctrl,pulse,filter,freq,pw,cut,read,read",
        "events,ctrl,pulse,filter,freq,pw,read,ad",
        "events,ctrl,pulse,filter,freq,pw,cut,read,ad,ad",
    ],
)
def test_an_order_line_names_the_base_phases_once_and_each_cell_phase_at_most_once(order):
    text = doc("[C4.i1,-]", ins=BARE, head=f"order:[{order}]\n")
    with pytest.raises(PlayError, match="order"):
        load(text)


# ---- global ---------------------------------------------------------------------------------


def two_voices(phrases: dict, shared: str, head: str = "") -> str:
    text = doc(phrases, ins=NOTE_ONLY, head=f"voices:2\ntempo:2\n{head}{shared}", rows=("[p1,p2]",))
    return text


@pytest.mark.parametrize("shared", ["", "global:[res]\n"])
def test_a_song_cell_fires_once_each_slot_reading_its_own_binder_s_notes(shared):
    """Slot a adds voice 1's N (D4 is 50), slot b voice 2's (E4 is 52), once a call: res is
    (0 + 50 + 52) mod 16 = 6, then 12, then 2."""
    phrases = {"p1": "[D4.i1 a.res:'v + N',-]", "p2": "[E4.i1 b.res:'v + N',-]"}
    text = two_voices(phrases, shared, "slots:{res:{a:{},b:{}}}\n")
    assert high(play(text, 3), D417) == [6, 12, 2]


@pytest.mark.parametrize("shared, expected", [("", [1, 2, 3, 4]), ("global:[res]\n", [1, 2, 2, 2])])
def test_a_stop_from_another_voice_ends_a_global_cell_s_binding_only(shared, expected):
    phrases = {"p1": "[-,- res:none,-]", "p2": "[C4.i1 res:'v + 1',-,-]"}
    assert high(play(two_voices(phrases, shared), 4), D417) == expected


@pytest.mark.parametrize("shared, expected", [("", [1, 2, 3, 4]), ("global:[vol]\n", [1, 2, 2, 2])])
def test_a_hold_from_another_voice_ends_a_global_cell_s_bindings(shared, expected):
    phrases = {"p1": "[-,- vol:hold,-]", "p2": "[C4.i1 vol:'v + 1',-,-]"}
    assert reg(play(two_voices(phrases, shared), 4), D418) == expected


def test_a_filter_program_ends_the_cutoff_bindings_of_the_other_voices_and_no_other_cell_s():
    ins = {
        "i1": "{ctrl:[{ctrl:65,note:+0,cut:v + 1,vol:v + 1},end]}",
        "i2": "{sr:240}",
    }
    phrases = {"p1": "[C4.i1,-*3]", "p2": "[-,C4.i2 run:f1,-,-]"}
    text = doc(
        phrases,
        ins=ins,
        head="voices:2\ntempo:2\n",
        rows=("[p1,p2]",),
        filter={"f1": "[{res:3},end]"},
    )
    frames = play(text, 6)
    assert cutoff(frames) == [1, 2, 2, 2, 2, 2] and reg(frames, D418) == [1, 2, 3, 4, 5, 6]


# ---- holds ----------------------------------------------------------------------------------

PULSES = "{pw:[{pw:10},{pw:20},{pw:30},{pw:40},end]}"


@pytest.mark.parametrize(
    "event, expected",
    [
        ("", [10, 20, 30, 40, 40, 40, 40, 40]),
        ("note", [0, 10, 10, 20, 20, 30, 30, 40]),
        ("note-1", [10, 10, 20, 20, 30, 30, 40, 40]),
    ],
)
def test_a_program_pauses_on_the_calls_of_its_holds_events(event, expected):
    """With tempo 2 the notes are calls 0, 2, 4, 6. A held call counts for nothing: the program
    started on one counts from its clock, a row due on one applies on the next call."""
    head = f"tempo:2\nholds:{{pw:[{event}]}}\n" if event else "tempo:2\n"
    frames = play(doc("[C4.i1,C4.i1 pw,C4.i1 pw,C4.i1 pw]", ins=PULSES, head=head), 8)
    assert pulse(frames) == expected


def test_a_layer_pauses_alone_and_a_kind_pauses_every_layer():
    ins = "{ctrl:c1,b.ctrl:c2}"
    ctrl = {"c1": "[{ad:1},{ad:2},{ad:3},{ad:4},end]", "c2": "[{sr:1},{sr:2},{sr:3},{sr:4},end]"}
    layers = "layers:{ctrl:[a,b]}\n"

    def grid(holds: str) -> tuple:
        text = doc("[C4.i1,-*3]", ins=ins, head=f"tempo:2\n{layers}{holds}", ctrl=ctrl)
        frames = play(text, 8)
        return reg(frames, ENV), reg(frames, SR)

    assert grid("holds:{b:[step]}\n") == ([1, 2, 3, 4, 4, 4, 4, 4], [0, 1, 1, 2, 2, 3, 3, 4])
    assert grid("holds:{ctrl:[step]}\n") == ([0, 1, 1, 2, 2, 3, 3, 4], [0, 1, 1, 2, 2, 3, 3, 4])


def test_a_filter_program_pauses_on_the_events_of_the_voice_that_started_it():
    """Voice 2 steps at calls 0 and 4 (vtempo 4), voice 1 at 0, 2, 4, 6; only voice 2's pause the
    program: rows at the clocks 0, 1, 2 from call 1, then 3 and 4 from call 5."""
    ins = {"i1": "{sr:240}", "i2": "{filter:f1}"}
    phrases = {"p1": "[C4.i1,-*3]", "p2": "[C4.i2 vtempo:4,-]"}
    text = doc(
        phrases,
        ins=ins,
        head="voices:2\ntempo:2\nholds:{filter:[step]}\n",
        rows=("[p1,p2]",),
        filter={"f1": "[{res:1},{res:2},{res:3},{res:4},{res:5},end]"},
    )
    assert high(play(text, 8), D417) == [0, 1, 2, 3, 3, 4, 5, 5]


def test_a_pause_on_row_events_waits_for_the_control_phase_to_apply_them():
    """The control rows with ad apply at calls 2 and 4, before the pulse phase: the pulse row due
    at call 2 applies at 3."""
    rows = "[{ctrl:65,note:+0},{wait:1,ad:1},{wait:1,ad:2},end]"
    ins = f"{{ctrl:{rows},pw:[{{pw:10}},{{pw:20}},{{pw:30}},end]}}"
    text = doc("[C4.i1,-*3]", ins=ins, head="tempo:2\nholds:{pw:[row.ad]}\n")
    assert pulse(play(text, 5)) == [10, 20, 20, 30, 30]


def test_a_pause_leaves_modulators_and_events_running():
    """The control program is held on every step; pw, bound by the first note, fires every call."""
    ins = "{ctrl:[{sr:1},{sr:2},{sr:3},{sr:4},end]}"
    text = doc("[C4.i1 pw:'v + 1',-*3]", ins=ins, head="tempo:2\nholds:{ctrl:[step]}\n")
    frames = play(text, 8)
    assert pulse(frames) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert reg(frames, SR) == [0, 1, 1, 2, 2, 3, 3, 4]


# ---- loops with calls -----------------------------------------------------------------------


def test_a_looping_song_with_calls_repeats_from_its_proved_loop():
    head = "tempo:2\nslots:{pw:{a:{mod:'phi % 5 + 1'}}}\ncalls:{frame:2,full:[1,0,0]}\n"
    text = doc("[C4.i1,D.i1,-]", ins="{sr:240}", head=head, ending=0)
    shown = render(read(text))
    n = len(shown.frames)
    more = play(text, 4 * n)
    assert np.array_equal(more[:n], shown.frames)
    assert np.array_equal(more[n:], np.tile(shown.frames[shown.loop :], (4 * n, 1))[: 3 * n])


def test_a_step_returns_none_where_the_last_frame_is_partial():
    player = Player(load(doc("[C4.i1,-]", ins=BARE, head="tempo:2\ncalls:{frame:3}\n")))
    assert [player.step() is None for _ in range(3)] == [False, True, True]
