"""Program templates play as their expansions (docs/semantics.md, Definitions and Programs)."""

import numpy as np
import pytest
from programs import expanded, signed, templated
from song import doc, pulse, reg
from test_inline import NAMED
from test_sid import SONGS
from unitracker_grammar.code import Call, CodeError

from unitracker_player import PlayError, play, read
from unitracker_player.compiler import _Compiler, compile_document
from unitracker_player.document import Param

PROGS = doc(
    {
        "p1": "[C4.i1 run:arp(3,4),-,D.i2,- run:arp(0,7),E.i1 run:pt(9),-*3]",
        "p2": "[G3.i2,- run:fl(200),A.i1 run:pa(3)*2,- run:fl(7),- run:pa(3),-*2]",
    },
    {
        "i1": "{ad:9,sr:240,ctrl:arp(7,4),pw:pt(300),filter:fl(500)," "restart:hr(1,15,0)}",
        "i2": "{ad:5,ctrl:[{ctrl:65,note:+0},{wait:2,run:arp(12,0)},end],pw:pw1,"
        "restart:hr(2,0,240)}",
    },
    rows=("[p1,p2]", "[p2,p1]"),
    head="voices:2\ntempo:2\n",
    mods={"walk(s)": "v + s"},
    restart={"hr(h,ad,sr)": "[{hold:h,gate:0,ad:ad,sr:sr},{},end]"},
    ctrl={"arp(a,b)": "[{ctrl:65,note:+a},{wait:1,note:-b}," "{wait:a,ctrl:64,note:+0},{loop:0}]"},
    pw={"pt(b)": "[{pw:b},{wait:1,pw:walk(b)},end]", "pw1": "pt(40)", "pa(b)": "walk(b)"},
    filter={"fl(c)": "[{cut:c,res:9,route:1,mode:16,vol:15},{wait:3,cut:walk(c)},end]"},
)


def _corpus():
    """The documents of the other tests, each made a document of templates, and PROGS."""
    for name, text in {**SONGS, "calls": NAMED["calls"]}.items():
        yield name, templated(text)
    yield "progs", PROGS
    yield "progs-templated", templated(expanded(PROGS))


def test_program_templates_are_read_with_their_parameters():
    document = read(PROGS)
    assert {k: v[:2] for k, v in document.forms.items()} == {
        "hr": ("restart", ("h", "ad", "sr")),
        "arp": ("ctrl", ("a", "b")),
        "pt": ("pw", ("b",)),
        "fl": ("filter", ("c",)),
        "pa": ("pw", ("b",)),
    }
    rows = document.forms["arp"][2].rows
    assert rows[0].keys[1] == ("note", ("rel", Param("a")))
    assert rows[1].keys == (("note", ("rel", Param("b", -1))),) and rows[2].wait == Param("a")
    assert document.programs["pw1"] == Call("pt", (40,)) and "arp" not in document.programs
    first = document.phrases["p1"][0]
    assert dict(first.commands)["run"] == Call("arp", (3, 4))


@pytest.mark.parametrize("name, text", list(_corpus()), ids=[n for n, _ in _corpus()])
def test_a_document_using_templates_plays_as_its_expansion(name, text):
    """Each call made a named program with each parameter its argument plays the same grid; a
    named document made templates over its literals plays as it did."""
    out = expanded(text)
    assert signed(text) and not signed(out), name
    ticks = 64
    grid = play(text, ticks)
    assert np.array_equal(play(out, ticks), grid)
    assert np.array_equal(play(templated(out), ticks), grid)


def test_a_call_is_compiled_once_and_is_its_program_s_identity():
    comp = _Compiler(read(PROGS), None)
    assert comp.program(Call("arp", (7, 4)), "ctrl") is comp.program(Call("arp", (7, 4)))
    assert comp.program(Call("arp", (7, 4))) is not comp.program(Call("arp", (7, 5)))
    assert comp.program("pw1") is comp.program(Call("pt", (40,)), "pw")
    compile_document(read(PROGS), _Compiler)


def test_a_binding_a_template_makes_keeps_phi_for_the_same_arguments():
    text = doc(
        "[C4.i1 run:pq(100),- run:pq(100),- run:pq(200),-]",
        head="tempo:2\n",
        mods={"ph(s)": "s + phi"},
        pw={"pq(b)": "ph(b)"},
    )
    assert pulse(play(text))[:8] == [100, 101, 102, 103, 200, 201, 202, 203]


def test_an_argument_wider_than_its_field_keeps_its_low_bits():
    def ad(arg: int) -> list:
        ctrl = {"ca(a)": "[{ctrl:65,ad:a},end]"}
        return reg(play(doc("[C4.i1,-]", f"{{ctrl:ca({arg})}}", ctrl=ctrl)), 5)

    assert ad(-1) == ad(255) and ad(300) == ad(44) and ad(44)[-1] == 44


@pytest.mark.parametrize(
    "edits, match",
    [
        ([("ctrl:arp(7,4)", "ctrl:arp(7)")], r"arp\(7\): arp takes 2 integer argument"),
        ([("ctrl:arp(7,4)", "ctrl:nope(7)")], "nope is no program template"),
        ([("ctrl:arp(7,4)", "ctrl:pt(7)")], "pt is a pw program, not a ctrl program"),
        ([("restart:hr(1,15,0)", "restart:arp(1,2)")], "arp is a ctrl program, not a restart"),
        ([(" run:arp(0,7)", " run:hr(0,7,1)")], "hr is no ctrl, pw or filter program template"),
        ([(" run:arp(0,7)", " run:walk(1)")], "walk is no ctrl, pw or filter program template"),
        (
            [("{pw:b},{wait:1,", "{pw:b},{wait:b,"), (" run:pt(9)", " run:pt(-1)")],
            r"program pt\(-1\): -1 ticks",
        ),
        ([("pw1:pt(40)", "pw1:pu(40)\n pu(b):pu(b)")], "a program template calls itself"),
    ],
    ids=[
        "arity",
        "unknown",
        "kind",
        "restart-kind",
        "run-restart",
        "run-modulator",
        "negative-wait",
        "recursive",
    ],
)
def test_calls_the_player_cannot_play_are_refused(edits, match):
    text = PROGS
    for old, new in edits:
        assert old in text
        text = text.replace(old, new)
    with pytest.raises(PlayError, match=match):
        play(text, 32)


@pytest.mark.parametrize(
    "text, match",
    [
        (
            doc(ctrl={"c1": "[{ad:a},end]"}, ins="{ctrl:c1}"),
            r"c1: reads \['a'\] and declares no",
        ),
        (doc("[C4.i1 pw:walk(a)]", mods={"walk(s)": "v + s"}), r"\['a'\] read outside a template"),
        (
            doc(ctrl={"arp(a,b)": "[{ctrl:a},end]"}, ins="{ctrl:arp(65,1)}"),
            r"arp\(a,b\): undeclared \[\], unread \['b'\]",
        ),
    ],
    ids=["named-program", "command", "unread"],
)
def test_a_parameter_is_read_only_in_its_program_template_s_rows(text, match):
    with pytest.raises(CodeError, match=match):
        play(text, 2)
