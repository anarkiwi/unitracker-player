"""Format 2.3: its sugar plays as its expansion written by hand, the prelude's templates, once
bindings, lag, the song's cutoff and a pulse or filter record that is a binding."""

import numpy as np
import pytest
from song import I1, cutoff, doc, freq, pulse
from unitracker_grammar import frequency as F
from unitracker_grammar.code import CodeError

from unitracker_player import Player, load, play, read
from unitracker_player.render import render

THREE = {"p1": "[C4.i1,-]", "p2": "[E4.i1,-*2]", "p3": "[G4.i1]"}
AR = {"ar(a,b)": "[{ctrl:65,note:+0},{note:+a},{note:+b},{loop:0}]"}
CHORD = "{ad:env,sr:240,ctrl:ar(lo,hi)}"


def _same(sugar: str, plain: str, ticks: int | None = None):
    assert np.array_equal(play(sugar, ticks), play(plain, ticks))


@pytest.mark.parametrize(
    "sugar, plain",
    [
        ("[C4.i1,[E,G]x2,-]", "[C4.i1,E,G,E,G,-]"),
        ("[C4.i1,[E|G|A],-]", "[C4.i1,E,G,E,A,-]"),
        ("[C4.i1,[E|G|A5],B]", "[C4.i1,E,G,E4,A5,B]"),
        ("[C4.i1,[[E,G]x2|A*2|B],C5]", "[C4.i1,E,G,E,G,A*2,E,G,E,G,B,C5]"),
    ],
)
def test_a_phrase_group_plays_as_its_events_written_out(sugar, plain):
    _same(doc(sugar), doc(plain))


@pytest.mark.parametrize(
    "items, rows",
    [
        (("[play:[p1]|play:[p2]|play:[p3]]",), ("[p1]", "[p2]", "[p1]", "[p3]")),
        (("[play:[p1],play:[p2]]x2", "[p3]"), ("[p1]", "[p2]", "[p1]", "[p2]", "[p3]")),
    ],
)
def test_a_song_group_plays_as_its_rows_written_out(items, rows):
    _same(doc(THREE, rows=items), doc(THREE, rows=rows))


@pytest.mark.parametrize(
    "items, ending, rows, loop",
    [
        (("[p3]", "s1x2", "[p2]"), 2, ("[p3]", "[p1]", "[p2]", "[p1]", "[p2]", "[p2]"), 5),
        (("[p3]", "s1x2", "[p2]"), 1, ("[p3]", "[p1]", "[p2]", "[p1]", "[p2]", "[p2]"), 1),
        (("s2", "[p1]"), 1, ("[p1]", "[p2]", "[p3]", "[p1]"), 3),
        (("[p3]", "s3=[play:[p2],play:[p1]]x2"), 1, ("[p3]", "[p2]", "[p1]", "[p2]", "[p1]"), 1),
    ],
)
def test_sections_play_as_their_rows_and_the_loop_item_s_first_row(items, ending, rows, loop):
    sections = {"s1": "[play:[p1],play:[p2]]", "s2": "[s1,play:[p3]]"}
    sugar = doc(THREE, rows=items, ending=ending, section=sections)
    _same(sugar, doc(THREE, rows=rows, ending=loop), 300)


@pytest.mark.parametrize(
    "phrase",
    ["[C4.chord(9,maj),-,E.chord(10,min),-]", f"[C4.chord(env,lo,hi)={CHORD}(9,maj),-,E.i2,-]"],
)
def test_an_instrument_template_call_plays_as_its_instrument(phrase):
    ins = {"i1": I1} | ({"chord(env,lo,hi)": CHORD} if "=" not in phrase else {})
    if "=" in phrase:
        ins["i2"] = "{ad:10,sr:240,ctrl:ar(3,7)}"
    plain = {
        "i1": I1,
        "i2": "{ad:9,sr:240,ctrl:[{ctrl:65,note:+0},{note:+4},{note:+7},{loop:0}]}",
        "i3": "{ad:10,sr:240,ctrl:[{ctrl:65,note:+0},{note:+3},{note:+7},{loop:0}]}",
    }
    _same(doc(phrase, ins, ctrl=AR), doc("[C4.i2,-,E.i3,-]", plain))


@pytest.mark.parametrize("entry", ["riff(G3,f3)+2", "riff(x,y)=[x.i1,-,y,E4](G3,f3)+2"])
def test_a_phrase_template_call_plays_as_its_phrase_transposed(entry):
    phrases = {"p1": "[C4.i1,-]"} | ({"riff(x,y)": "[x.i1,-,y,E4]"} if "=" not in entry else {})
    _same(doc(phrases, rows=(f"[{entry}]",)), doc(rows=("[[G3.i1,-,f3,E4]+2]",)))


def test_a_chord_name_stands_for_its_intervals_among_a_call_s_arguments():
    _same(doc("[C4.i1 run:ar(maj),-*3]", ctrl=AR), doc("[C4.i1 run:ar(4,7),-*3]", ctrl=AR))


@pytest.mark.parametrize(
    "command, read_out, expected",
    [
        ("pw:tremolo(100,300,2)", pulse, [100, 100, 300, 300] * 6),
        ("pw:pwm(0,8,2)", pulse, [0, 2, 4, 6, 8, 6, 4, 2] * 3),
        ("pw:glide(5)", pulse, list(range(5, 125, 5))),
        ("cut:sweep(10,20,3)", cutoff, [10, 13, 16, 19] + [20] * 20),
        ("freq:vibrato(2,1,2)", freq, [F(48) + d for d in [0, 0] + [0, 2, 0, -2] * 5 + [0, 2]]),
    ],
)
def test_a_prelude_template_plays_its_expression(command, read_out, expected):
    assert read_out(play(doc(f"[C4.i1 {command},-*3]"))) == expected


def test_the_prelude_slide_approaches_its_target_by_its_step():
    values, v = [F(48)] * 6, F(48)
    for _ in range(18):
        v += max(min(F(64) - v, 100), -100)
        values.append(v)
    assert freq(play(doc("[C4.i1,E freq:slide(12,100),-*2]"))) == values


def test_a_document_defining_a_prelude_name_is_refused():
    with pytest.raises(CodeError, match="prelude"):
        read(doc(mods={"glide(s)": "v + s"}, phrase="[C4.i1 pw:glide(2)]"))


@pytest.mark.parametrize(
    "phrase, read_out, expected",
    [
        ("[C4.i1 freq:'F(N) + 100'!,-*3]", freq, [F(48) + 100] + [F(48)] * 23),
        ("[C4.i1 pw:'v + 10'!,-,E pw:'v + 10'!,-]", pulse, [10] * 12 + [20] * 12),
        ("[C4.i1 pw:'phi + 100',-,- pw:'phi + 100'!,-]", pulse, list(range(100, 112)) + [100] * 12),
        ("[C4.i1 pw:800!,-*3]", pulse, [800] * 24),
    ],
)
def test_a_command_s_once_binding_fires_once_then_its_slot_is_unbound(phrase, read_out, expected):
    assert read_out(play(doc(phrase))) == expected


def test_a_row_s_once_binding_fires_on_its_row_s_tick_alone():
    ins = "{ad:9,sr:240,ctrl:[{ctrl:65,note:+0,cut:'v + 7'!},{wait:2,cut:'v + 7'!},end]}"
    assert cutoff(play(doc(ins=ins))) == [7] * 3 + [14] * 21


def test_an_instrument_field_s_once_binding_fires_on_each_note():
    ins = "{ad:9,sr:240,ctrl:[{ctrl:65,note:+0},end],pw:'v + 3'!}"
    assert pulse(play(doc("[C4.i1,E,G,-]", ins))) == [3] * 6 + [6] * 6 + [9] * 12


def test_a_code_once_binding_snaps_then_the_note_s_frequency_plays():
    snap = "asm{lda #1; sta v; lda #0; sta v+1; rts}!"
    ins = f"{{ad:9,sr:240,ctrl:[{{ctrl:65,note:+0}},end],freq:{snap}}}"
    assert freq(play(doc("[C4.i1,-,E,-]", ins))) == [1] + [F(48)] * 11 + [1] + [F(52)] * 11


def test_a_once_binding_leaves_the_cell_s_other_slots_bound():
    head = "slots:{freq:{a:{},b:{}}}\n"
    text = doc("[C4.i1 a.freq:'F(N) + 50' b.freq:'v + 1'!,-*3]", head=head)
    assert freq(play(text)) == [F(48) + 51] + [F(48) + 50] * 23


def test_a_held_once_binding_fires_on_its_first_unheld_tick():
    text = doc("[C4.i1 pw:'v + 10'!,-*3]", head="slots:{pw:{a:{hold:[note]}}}\n")
    assert pulse(play(text)) == [0] + [10] * 23


REGS = list(range(1, 26))


@pytest.mark.parametrize("k", [1, 2])
@pytest.mark.parametrize("regs", [None, REGS])
def test_lag_writes_each_tick_the_registers_computed_k_ticks_before(k, regs):
    head = f"regs:[{','.join(map(str, regs))}]\n" if regs else ""
    plain, lagged = play(doc(head=head)), play(doc(head=f"{head}lag:{k}\n"))
    assert lagged.shape == plain.shape
    assert lagged[:k].tolist() == [regs or [0] * 25] * k
    assert np.array_equal(lagged[k:], plain[:-k])


def test_a_lagging_player_s_regs_are_the_registers_each_step_writes():
    player = Player(load(doc(head="lag:1\n")))
    first, second = player.step(), player.step()
    assert first == [0] * 25 and second == player.regs and second[4] == 65


@pytest.mark.parametrize("k", [1, 3])
def test_a_lagging_looping_song_repeats_from_its_proved_loop(k):
    ins = "{ad:9,sr:240,ctrl:[{ctrl:65,note:+0,pw:'16*abs(phi % 40 - 20) + 512'},end]}"
    text = doc("[C4.i1,D*2,-*2]", ins, ending=0, head=f"lag:{k}\n")
    r = render(read(text))
    period = len(r.frames) - r.loop
    ticks = len(r.frames) + 3 * period
    index = [t if t < len(r.frames) else r.loop + (t - r.loop) % period for t in range(ticks)]
    assert np.array_equal(play(text, ticks), r.frames[index])


def _two(p1: str, p2: str, ins: str = I1, shared: bool = True) -> str:
    head = "voices:2\n" + ("global:[cut]\n" if shared else "")
    return doc({"p1": p1, "p2": p2}, ins, rows=("[p1,p2]",), head=head)


@pytest.mark.parametrize("shared, expected", [(True, [6] * 18), (False, list(range(7, 25)))])
def test_a_global_cut_stop_from_another_voice_ends_the_binding(shared, expected):
    text = _two("[C4.i1 cut:'v + 1',-*3]", "[-,- cut:none,-*2]", shared=shared)
    assert cutoff(play(text)) == list(range(1, 7)) + expected


@pytest.mark.parametrize("shared, held", [(True, [300] * 18), (False, [100, 200, 300] * 6)])
def test_a_global_cut_hold_from_another_voice_stops_the_filter_program(shared, held):
    ins = (
        "{ad:9,sr:240,ctrl:[{ctrl:65,note:+0},end],filter:[{cut:100},{cut:200},{cut:300},{loop:0}]}"
    )
    text = _two("[C4.i1,-*3]", "[-,- cut:hold,-*2]", ins, shared)
    assert cutoff(play(text)) == [100, 200, 300] * 2 + held


def test_a_global_cut_binding_reads_the_notes_of_the_voice_that_bound_it():
    text = _two("[C4.i1 cut:'N',-*3]", "[E4.i1,G,A,B]")
    assert cutoff(play(text)) == [48] * 24


@pytest.mark.parametrize("binding", ["10*phi + 100", "pwm(0,8,2)"])
def test_a_named_binding_record_in_an_instrument_restarts_phi_as_its_field(binding):
    ctrl = "ctrl:[{ctrl:65,note:+0},end]"
    named = doc("[C4.i1,-,E,-]", f"{{ad:9,sr:240,{ctrl},pw:pw1}}", pw={"pw1": binding})
    field = doc("[C4.i1,-,E,-]", f"{{ad:9,sr:240,{ctrl},pw:{binding}}}")
    frames = play(named)
    assert np.array_equal(frames, play(field)) and pulse(frames)[12] == pulse(frames)[0]


def test_a_run_of_a_named_binding_record_keeps_phi_as_its_program():
    text = doc("[C4.i1 run:pw1,-,E run:pw1,-]", pw={"pw1": "10*phi + 100"})
    assert pulse(play(text)) == [10 * t + 100 for t in range(24)]
