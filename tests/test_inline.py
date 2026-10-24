"""Definitions inline at their one use play as their named forms (docs/semantics.md,
Definitions)."""

import numpy as np
import pytest
from conftest import FIXTURES, lifted
from inline import DEFINED, inlined, parser
from song import doc
from test_code_play import ADD, TABLE, block
from test_sid import SONGS
from unitracker_grammar.code import CodeError

from unitracker_player import PlayError, play, read
from unitracker_player.document import Code, Instrument, Program

GATE = "ctrl: [{ctrl: 65, note: +0}, end]"
NAMED = {
    "programs": doc(
        {"p1": "[C4.i1!run:pw1, D4.i2!run:c2, -!run:f1, E4.i1!run:pw2*2]", "p2": "[G3.i2, -]"},
        {
            "i1": "{ad: 9, sr: 240, ctrl: c1, pw: pw1, filter: f2, restart: r1}",
            "i2": "{ad: 5, ctrl: [{ctrl: 65, note: +0}, {wait: 2, run: c2}, end], pw: pw2}",
        },
        rows=("[p1]", "[p2-2]", "[p1+3]"),
        head="tempo: 2\ninit: [C3.i2!pw:'v + 40']\n",
        restart={"r1": "[{hold: 1, gate: 0}, {}, end]"},
        ctrl={
            "c1": "[{ctrl: 65, note: +0}, {wait: 1, note: +7, run: pw2}, end]",
            "c2": "[{ctrl: 17}, end]",
        },
        pw={"pw1": "v + 3*φ", "pw2": "[{pw: 800}, {wait: 2, pw: v + 9}, {loop: 1}]"},
        filter={"f1": "[{cut: 300, res: 9, route: 1, mode: 16, vol: 15}, end]", "f2": "v + 2"},
    ),
    "calls": (
        doc(
            "[C4.i1!pw:add(-7)!wob.freq:vib(3,40,6), D4!b.pw:cnt()!cut:walk(30),"
            " -!freq:note(), E5, -*6]",
            f"{{ad: 9, sr: 240, {GATE}, pw: add(300), filter: walk(5),"
            " base.freq: (φ < 1)*F(N) + (φ >= 1)*v}",
            head="clock: {cycles_per_tick: 19656, clock_hz: 985000}\ntempo: 2\n"
            "slots: {freq: {base: {}, wob: {}}, pw: {a: {mod: walk(2)}, b: {}}}\n"
            "state: {freq: {t: 1}, pw: {q: 1}}\n",
            mods={
                "vib(c, s, w)": block(
                    "inc t\nlda t\ncmp #w\nbcc go\nlda #0\nsta t\ngo:\ncmp #c\nbcs dn\nclc\n"
                    "lda v\nadc #<s\nsta v\nlda v+1\nadc #>s\nsta v+1\nrts\ndn:\nsec\nlda v\n"
                    "sbc #<s\nsta v\nlda v+1\nsbc #>s\nsta v+1\nrts"
                ),
                "add(d)": block(ADD),
                "cnt()": block("inc q\nlda q\nsta v\nlda #0\nsta v+1\nrts"),
                "note()": block(TABLE),
                "walk(s)": "v + s",
            },
        )
    ),
}


def _documents():
    yield from SONGS.items()
    yield from NAMED.items()


def _references(text: str) -> set:
    return {
        t.type for t in parser().parse(text).scan_values(lambda t: t.type in DEFINED | {"CALL"})
    }


@pytest.mark.parametrize("name, text", list(_documents()), ids=[n for n, _ in _documents()])
def test_a_document_with_every_definition_inline_plays_as_its_named_form(name, text):
    out = inlined(text)
    assert not _references(out) and "def:" not in out and "\nmod:" not in out, name
    ticks = 64
    assert np.array_equal(play(out, ticks), play(text, ticks))


@pytest.mark.parametrize("name", FIXTURES)
def test_a_lifted_fixture_inlined_plays_as_lifted(name):
    text = lifted(name)
    out = inlined(text)
    assert not _references(out)
    assert np.array_equal(play(out, 600), play(text, 600))


def test_inline_definitions_are_read_as_anonymous_named_ones():
    document = read(inlined(NAMED["programs"]))
    event = document.timeline[0][0].phrase[0]
    assert isinstance(event.ins, Instrument) and isinstance(event.ins.restart, Program)
    assert dict(event.commands)["run"][0] == "pw" and document.instruments == {}
    calls = read(inlined(NAMED["calls"]))
    first = calls.timeline[0][0].phrase[0]
    assert isinstance(dict(first.commands)["wob.freq"], Code) and calls.templates == {}


def test_an_anonymous_body_keeps_phi_for_the_same_text():
    """Rebinding the same body keeps φ; another text restarts it."""
    phi = "asm{lda phi; sta v; rts}"
    other = "asm{lda phi; sta v; clc; rts}"
    text = doc(f"[C4.i1!pw:{phi}, -!pw:{phi}, -!pw:{other}, -]", head="tempo: 2\n")
    assert play(text)[:, 2].tolist() == [0, 1, 2, 3, 0, 1, 2, 3]


@pytest.mark.parametrize(
    "text, error, match",
    [
        (doc("[C4.i1!pw:asm{lda #c; sta v; rts}]"), PlayError, "reads no parameter"),
        (
            doc("[C4.i1!pw:asm{t:; rts}]", head="state: {pw: {t: 1}}\n"),
            CodeError,
            "label 't' is a symbol",
        ),
        (doc("[C4.i1!pw:asm{lda u; rts}]"), PlayError, "unknown symbol u"),
    ],
    ids=["parameter", "label", "symbol"],
)
def test_anonymous_bodies_the_player_cannot_play_are_refused(text, error, match):
    with pytest.raises(error, match=match):
        play(text, 2)


def test_a_command_and_a_slot_s_default_bind_quoted_expressions():
    head = "tempo: 2\nslots: {pw: {a: {mod: 'v + 1'}, b: {}}}\n"
    text = doc("[C4.i1, -!b.pw:'2*v']", head=head)
    assert play(text, 4)[:, 2].tolist() == [1, 2, 6, 14]
