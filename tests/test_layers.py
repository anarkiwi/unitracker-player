"""Format 2.4's layers: a voice's concurrent programs of one kind, each walked by its own cursor
and run in declared order (docs/semantics.md, Layers)."""

import numpy as np
import pytest
from song import cutoff, doc, freq, pulse, reg
from unitracker_grammar import frequency as F
from unitracker_grammar.document import printed, read

from unitracker_player import play
from unitracker_player.sequencer import PlayError

AD, VOL = 5, 24
WALKS = {
    "c1": "[{note:+12},{note:+0},{loop:0}]",
    "c2": "[{ctrl:65},{ctrl:17},{ctrl:33},{loop:0}]",
}


def layered(layers: str = "ctrl:[a,b]", **kw) -> str:
    """The format 2.4 document of :func:`song.doc`'s arguments and a ``layers`` line."""
    head = kw.pop("head", "")
    at = head.find("order:") if "order:" in head else len(head)
    head = head[:at] + f"layers:{{{layers}}}\n" + head[at:]
    return printed(read(doc(head=head, **kw).replace("unitracker:2.3", "unitracker:2.4", 1)))


def test_two_layers_play_as_their_one_program_walked_in_lockstep():
    """Periods 2 and 3 in two layers are the one program of period 6 that walks both."""
    lockstep = "[" + ",".join(
        f"{{ctrl:{c},note:+{n}}}" for c, n in zip([65, 17, 33] * 2, [12, 0] * 3)
    )
    one = doc(ins="{ctrl:c3}", ctrl={"c3": lockstep + ",{loop:0}]"})
    two = layered(ins="{ctrl:c1,b.ctrl:c2}", ctrl=WALKS)
    assert " i1:{ctrl:c1,b.ctrl:c2}\n" in two
    np.testing.assert_array_equal(play(two, 30), play(one, 30))
    assert reg(play(two, 6), 4) == [65, 17, 33, 65, 17, 33]


@pytest.mark.parametrize("layers, ad", [("ctrl:[a,b]", 2), ("ctrl:[b,a]", 1)])
def test_a_later_layer_s_keys_replace_an_earlier_one_s(layers, ad):
    text = layered(
        layers, ins="{a.ctrl:c1,b.ctrl:c2}", ctrl={"c1": "[{ad:1},end]", "c2": "[{ad:2},end]"}
    )
    assert reg(play(text, 2), AD) == [ad, ad]


def test_each_layer_runs_over_every_voice_before_the_next_layer():
    """Voice 2's first layer runs before voice 1's second, so voice 1's second sets the volume."""
    ctrl = {"c1": "[{vol:1},end]", "c2": "[{vol:2},end]", "c3": "[{vol:3},end]"}
    ins = {"i1": "{b.ctrl:c2}", "i2": "{ctrl:c3}"}
    phrase = {"p1": "[C4.i1,-*3]", "p2": "[C4.i2,-*3]"}
    text = layered(phrase=phrase, ins=ins, ctrl=ctrl, rows=("[p1,p2]",), head="voices:2\n")
    assert reg(play(text, 2), VOL) == [2, 2]


def test_a_note_starts_only_the_layers_its_instrument_names():
    """i2 restarts layer b only; layer a's walk goes on from where it is."""
    ins = {"i1": "{ctrl:c1,b.ctrl:c2}", "i2": "{b.ctrl:c4}"}
    ctrl = WALKS | {"c4": "[{ad:7},end]"}
    text = layered(phrase="[C4.i1,-,D.i2,-*3]", ins=ins, ctrl=ctrl, head="tempo:3\n")
    frames = play(text, 15)
    notes = [48 + 12 * (t % 2 == 0) for t in range(6)] + [
        50 + 12 * (t % 2 == 0) for t in range(6, 15)
    ]
    assert freq(frames) == [F(n) for n in notes]
    assert reg(frames, 4)[:6] == [65, 17, 33, 65, 17, 33] and set(reg(frames, 4)[6:]) == {33}
    assert reg(frames, AD) == [0] * 6 + [7] * 9


def test_a_run_starts_and_none_stops_one_layer():
    """``b.run`` starts layer b; ``b.run:none`` stops it, ``run:none`` stops layer a."""
    ins = "{ctrl:c1}"
    phrase = "[C4.i1 b.run:c2,- b.run:none,-,- run:none,-]"
    text = layered(phrase=phrase, ins=ins, ctrl=WALKS, head="tempo:2\n")
    frames = play(text, 10)
    assert reg(frames, 4) == [65, 17, 17, 17, 17, 17, 17, 17, 17, 17]
    assert freq(frames) == [F(60), F(48)] * 3 + [F(48)] * 4


def test_a_row_runs_a_program_in_the_layer_its_prefix_names():
    """Layer b runs after layer a in the tick, so the program a's row starts there plays at once."""
    ctrl = {"c1": "[{b.run:c2,run:c3},end]", "c2": WALKS["c2"], "c3": "[{ad:9},end]"}
    text = layered(ins="{ctrl:c1}", ctrl=ctrl)
    assert reg(play(text, 4), 4) == [65, 17, 33, 65]
    assert reg(play(text, 4), AD) == [9, 9, 9, 9]


def test_a_filter_program_stops_only_its_own_layer_on_the_other_voices():
    """Voice 2's layer y leaves voice 1's layer x running; its layer x stops voice 1's."""
    filters = {"f1": "[{s.cut:v + 1},{loop:0}]", "f2": "[{t.cut:v + 100},{loop:0}]"}
    ins = {"i1": "{filter:f1}", "i2": "{y.filter:f2}", "i3": "{filter:f2}"}
    head = "voices:2\nslots:{cut:{s:{},t:{}}}\n"
    phrase = {"p1": "[C4.i1,-*3]", "p2": "[-,C4.i2,C4.i3,-]"}
    text = layered(
        "filter:[x,y]", phrase=phrase, ins=ins, filter=filters, rows=("[p1,p2]",), head=head
    )
    cut = cutoff(play(text, 24))
    assert cut[:6] == [1, 2, 3, 4, 5, 6]
    assert np.diff(cut[6:12]).tolist() == [101] * 5
    assert np.diff(cut[12:24]).tolist() == [100] * 11


def test_a_pulse_hold_stops_every_pulse_layer():
    pws = {"pw1": "[{pw:v + 1},{loop:0}]", "pw2": "[{q.pw:v + 10},{loop:0}]"}
    ins = "{pw:pw1,y.pw:pw2}"
    head = "tempo:2\nslots:{pw:{s:{},q:{}}}\n"
    text = layered("pw:[x,y]", phrase="[C4.i1,D,- pw:hold,-]", ins=ins, pw=pws, head=head)
    assert pulse(play(text, 8)) == [11, 22, 33, 44, 44, 44, 44, 44]


@pytest.mark.parametrize(
    "flag, expected", [("", [1, 2, 1, 2, 3, 4, 1, 2]), (" pw", [1, 2, 3, 4] * 2)]
)
def test_the_pw_flag_keeps_every_pulse_layer(flag, expected):
    walk = "[" + ",".join(f"{{pw:{k}}}" for k in range(1, 5)) + ",{loop:0}]"
    text = layered(
        "pw:[x,y]",
        phrase=f"[C4.i1,D{flag},-*2]",
        ins="{y.pw:pw1}",
        pw={"pw1": walk},
        head="tempo:2\n",
    )
    assert pulse(play(text, 8)) == expected


def test_a_pulse_field_s_prefix_names_a_layer_or_else_a_slot():
    ins = "{s.pw:5,y.pw:pw2}"
    text = layered(
        "pw:[x,y]", ins=ins, pw={"pw2": "[{q.pw:v + 10},end]"}, head="slots:{pw:{s:{},q:{}}}\n"
    )
    assert pulse(play(text, 3)) == [15, 25, 35]


@pytest.mark.parametrize(
    "layers, ins, slots, error, match",
    [
        ("ctrl:[a,b]", "{c.ctrl:[{ad:1},end]}", "", PlayError, "no layer c"),
        ("ctrl:[a,a]", "{ctrl:[{ad:1},end]}", "", PlayError, "a layer twice"),
        ("pw:[s,y]", "{pw:5}", "slots:{pw:{s:{}}}\n", PlayError, "both a pw layer and a slot"),
        ("ctrl:[a,b]", "{b.ctrl:[{ad:1},end],b.ctrl:[{ad:2},end]}", "", PlayError, "twice"),
    ],
)
def test_a_layer_must_be_declared_once_and_started_once(layers, ins, slots, error, match):
    with pytest.raises(error, match=match):
        play(layered(layers, ins=ins, head=slots), 1)


def test_a_run_of_an_undeclared_layer_is_refused():
    with pytest.raises(PlayError, match="no layer c"):
        play(layered(phrase="[C4.i1 c.run:none]", ins="{ad:1}"), 1)


def test_a_row_event_names_the_rows_of_every_control_layer():
    ins = "{ctrl:c1,b.ctrl:c2,pw:10*phi + 1}"
    ctrl = {"c1": "[{note:+0},{loop:0}]", "c2": "[{hold:2,ad:5},{loop:0}]"}
    text = layered(ins=ins, ctrl=ctrl, head="slots:{pw:{a:{reset:[row.ad]}}}\n")
    assert pulse(play(text, 5)) == [1, 11, 1, 11, 1]
