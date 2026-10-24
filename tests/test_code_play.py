"""Format 1.23 documents: the state line and code templates played (docs/semantics.md, Code
modulators)."""

import pytest
from song import cutoff, doc, freq, pulse
from unitracker_grammar import CanonicalError
from unitracker_grammar import frequency as F
from unitracker_grammar.code import CodeError

from unitracker_player import PlayError, play, read
from unitracker_player.cli import sid_main
from unitracker_player.document import Code, Expr
from unitracker_player.render import RenderError, render

GATE = "ctrl: [{ctrl: 65, note: +0}, end]"
ADD = "clc\nlda v\nadc #<d\nsta v\nlda v+1\nadc #>d\nsta v+1\nrts"
COUNT = "inc t\nlda t\nsta v\nlda #0\nsta v+1\nrts"
VIB = """lda t
cmp #c
bcs down
clc
lda v
adc #<s
sta v
lda v+1
adc #>s
sta v+1
jmp next
down:
sec
lda v
sbc #<s
sta v
lda v+1
sbc #>s
sta v+1
next:
inc t
lda t
cmp #w
bcc done
lda #0
sta t
done:
rts"""


def block(src: str) -> str:
    """A code template's record value: ``|`` then its statements at the record's indentation
    plus 2."""
    return "|\n" + "\n".join("          " + line for line in src.splitlines())


def v23(text: str, version: str = "1.23") -> str:
    return text.replace("unitracker: 1.21", f"unitracker: {version}", 1)


def test_the_state_line_and_code_templates_are_read():
    head = "state: {freq: {t: 1}, cut: {q: 2, r: 1}}\n"
    text = v23(doc(head=head, mod={"cnt": block(COUNT), "e": "v + 1"}))
    document = read(text)
    assert document.header.state == {"freq": (("t", 1),), "cut": (("q", 2), ("r", 1))}
    assert isinstance(document.templates["cnt"], Code)
    assert isinstance(document.templates["e"], Expr)
    assert len(document.templates["cnt"].statements) == 6


def test_a_state_variable_declared_twice_is_refused():
    text = v23(doc(head="state: {freq: {t: 1}, pw: {t: 2}}\n"))
    with pytest.raises(CanonicalError, match="declared twice"):
        read(text)


def test_a_label_naming_a_state_variable_is_refused():
    text = v23(doc(head="state: {pw: {t: 1}}\n", mod={"m": block("t:\nrts")}))
    with pytest.raises(CodeError, match="label 't' is a symbol"):
        read(text)


@pytest.mark.parametrize(
    "head, mod",
    [("state: {freq: {t: 1}}\n", {"e": "v"}), ("", {"m": block("rts")})],
    ids=["state", "code"],
)
def test_state_and_code_templates_are_format_1_23_s(head, mod):
    text = v23(doc(head=head, mod=mod), "1.22")
    with pytest.raises(PlayError, match="format 1.23"):
        play(text, 4)


@pytest.mark.parametrize("cell, d", [("pw", 3000), ("freq", 40000)])
def test_a_16_bit_add_plays_as_its_expression(cell, d):
    """``v + d`` in code is the expression's value wrapped to the cell's width."""
    ins = f"{{ad: 9, sr: 240, {GATE}, {cell}: add {{d: {d}}}}}"
    coded = v23(doc(ins=ins, mod={"add": block(ADD)}))
    expressed = v23(doc(ins=ins, mod={"add": "v + d"}), "1.22")
    read_out = pulse if cell == "pw" else freq
    mask = 0xFFF if cell == "pw" else 0xFFFF
    assert read_out(play(coded, 8)) == read_out(play(expressed, 8))
    assert read_out(play(coded, 8)) == [(k * d) & mask for k in range(1, 9)]


def _vibrato(version: str, template: str, head: str = "") -> str:
    ins = (
        f"{{ad: 9, sr: 240, {GATE}, base.freq: (φ < 1)*F(N) + (φ >= 1)*v, "
        "wob.freq: vib {c: 3, s: 40, w: 6}}"
    )
    slots = "slots: {freq: {base: {}, wob: {}}}\n"
    return v23(doc(ins=ins, head=slots + head, mod={"vib": template}), version)


def test_a_goattracker_vibrato_on_a_state_byte_plays_as_its_expression():
    """t counts 0..w-1 and v rises by s for t < c, else falls."""
    coded = _vibrato("1.23", block(VIB), "state: {freq: {t: 1}}\n")
    expressed = _vibrato("1.22", "v + s*(φ % w < c) - s*(φ % w >= c)")
    base = F(48)
    steps = [40, 40, 40, -40, -40, -40] * 2
    expected = [base + sum(steps[: k + 1]) for k in range(12)]
    assert freq(play(coded, 12)) == freq(play(expressed, 12)) == expected


def test_each_voice_has_its_own_state_kept_across_notes_and_bindings():
    """A note rebinds the slot (φ back at 0) but t counts on; voice 2 starts its own t."""
    ins = f"{{ad: 9, sr: 240, {GATE}, pw: cnt}}"
    phrases = {"p1": "[C4.i1, E4.i1]", "p2": "[-, C4.i1]"}
    head = "voices: 2\ntempo: 2\nstate: {pw: {t: 1}}\n"
    frames = play(
        v23(doc(phrases, ins=ins, head=head, rows=("[p1, p2]",), mod={"cnt": block(COUNT)}))
    )
    assert pulse(frames, 1) == [1, 2, 3, 4]
    assert pulse(frames, 2) == [0, 0, 1, 2]


def test_slots_of_a_cell_share_its_state_and_a_held_slot_runs_nothing():
    """Two counting slots add 2 a tick; b held on each step neither counts nor writes."""
    ins = f"{{ad: 9, sr: 240, {GATE}, a.pw: cnt, b.pw: cnt}}"
    head = "tempo: 3\nslots: {pw: {a: {}, b: {hold: [step]}}}\nstate: {pw: {t: 1}}\n"
    text = v23(doc("[C4.i1, -]", ins=ins, head=head, mod={"cnt": block(COUNT)}))
    assert pulse(play(text)) == [1, 3, 5, 6, 8, 10]


def test_cut_has_one_state_for_the_song_whichever_voice_binds_it():
    """Voice 2's binding replaces voice 1's and counts on from its t."""
    phrases = {"p1": "[C4.i1!cut:x1, -*3]", "p2": "[-*2, C4.i1!cut:x1, -]"}
    head = "voices: 2\ntempo: 1\nstate: {cut: {t: 1}}\n"
    text = doc(phrases, head=head, rows=("[p1, p2]",), mod={"cnt": block(COUNT)}, x={"x1": "cnt"})
    assert cutoff(play(v23(text))) == [1, 2, 3, 4]


def test_phi_and_the_notes_are_the_slot_s():
    """phi restarts on the slot's reset events; nn is N with the row's +12, ne the event note."""
    phi = "lda phi\nsta v\nlda phi+1\nsta v+1\nrts"
    ins = f"{{ad: 9, sr: 240, {GATE}, pw: ph}}"
    head = "tempo: 3\nslots: {pw: {a: {reset: [step]}}}\n"
    text = v23(doc("[C4.i1, -]", ins=ins, head=head, mod={"ph": block(phi)}))
    assert pulse(play(text)) == [0, 1, 2, 0, 1, 2]
    notes = "lda nn\nsta v\nlda ne\nsta v+1\nrts"
    ins = "{ad: 9, sr: 240, ctrl: [{ctrl: 65, note: +12}, end], freq: nt}"
    text = v23(doc("[C4.i1, -]", ins=ins, mod={"nt": block(notes)}))
    assert freq(play(text, 1)) == [48 << 8 | 60]


def test_bindings_by_default_by_instance_and_by_bag():
    """A slot's default binds a template without parameters by name; an x instance and a
    command bind it with its bag."""
    head = "slots: {pw: {a: {mod: cnt}, b: {}}}\nstate: {pw: {t: 1}}\n"
    text = v23(
        doc(
            "[C4.i1!b.pw:x1, -]",
            head=head,
            mod={"cnt": block(COUNT), "add": block(ADD)},
            x={"x1": "add {d: 100}"},
        )
    )
    assert pulse(play(text, 3)) == [101, 102, 103]


@pytest.mark.parametrize(
    "mod, ins, match",
    [
        ({"m": block("lda t\nsta v\nrts")}, "pw: m", "t is freq's state, not pw's"),
        ({"m": block("lda u\nsta v\nrts")}, "pw: m", "unknown symbol u"),
        ({"m": block(ADD)}, "pw: m {d: 1, e: 2}", r"unknown \['e'\]"),
        ({"m": block(ADD)}, "pw: m {d: [1, 2]}", "a parameter is an integer"),
    ],
    ids=["other-cell", "unknown", "extra", "list"],
)
def test_bindings_the_template_cannot_play_are_refused(mod, ins, match):
    head = "state: {freq: {t: 1}}\n"
    text = v23(doc(ins=f"{{ad: 9, sr: 240, {GATE}, {ins}}}", head=head, mod=mod))
    with pytest.raises(PlayError, match=match):
        play(text, 2)


def test_a_looping_song_with_a_code_modulator_needs_ticks(tmp_path):
    """Its loop is not proved: render and unitracker-sid refuse it without ticks."""
    text = v23(
        doc(ins=f"{{ad: 9, sr: 240, {GATE}, pw: add {{d: 5}}}}", ending=0, mod={"add": block(ADD)})
    )
    with pytest.raises(RenderError, match="--ticks"):
        render(read(text))
    rendered = render(read(text), 30)
    assert rendered.loop is None and (rendered.frames == play(text, 30)).all()
    path = tmp_path / "song.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        sid_main([str(path)])
    assert exc.value.code == 2
