"""The state line and code templates played (docs/semantics.md, Code
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


def block(src: str, indent: int = 4) -> str:
    """A code template's record value: ``|`` then its statements at the record's indentation
    plus 2 (4 in the top-level mod section)."""
    return "|\n" + "\n".join(" " * indent + line for line in src.splitlines())


def test_the_state_line_and_code_templates_are_read():
    head = "state: {freq: {t: 1}, cut: {q: 2, r: 1}}\n"
    text = doc(head=head, mods={"cnt()": block(COUNT), "e(s)": "v + s"})
    document = read(text)
    assert document.header.state == {"freq": (("t", 1),), "cut": (("q", 2), ("r", 1))}
    assert isinstance(document.templates["cnt"], Code)
    assert isinstance(document.templates["e"], Expr)
    assert len(document.templates["cnt"].statements) == 6
    assert document.signatures == {"cnt": (), "e": ("s",)}


def test_a_state_variable_declared_twice_is_refused():
    text = doc(head="state: {freq: {t: 1}, pw: {t: 2}}\n")
    with pytest.raises(CanonicalError, match="declared twice"):
        read(text)


@pytest.mark.parametrize(
    "mods, match",
    [
        ({"m()": block("t:\nrts")}, "label 't' is a symbol"),
        ({"m(d, e)": block(ADD)}, r"m\(d, e\): undeclared \[\], unread \['e'\]"),
        ({"m()": block(ADD)}, r"m\(\): undeclared \['d'\]"),
        ({"w(s, k)": "v + s"}, r"unread \['k'\]"),
        ({"m(t)": block("lda #t\nrts")}, r"\['t'\] are state variables"),
    ],
    ids=["label", "unread", "undeclared", "expression", "state"],
)
def test_a_template_s_labels_and_signature_are_checked(mods, match):
    text = doc(head="state: {pw: {t: 1}}\n", mods=mods)
    with pytest.raises(CodeError, match=match):
        read(text)


@pytest.mark.parametrize("cell, d", [("pw", 3000), ("freq", 40000)])
def test_a_16_bit_add_plays_as_its_expression(cell, d):
    """``v + d`` in code is the expression's value wrapped to the cell's width."""
    ins = f"{{ad: 9, sr: 240, {GATE}, {cell}: add({d})}}"
    coded = doc(ins=ins, mods={"add(d)": block(ADD)})
    called = doc(ins=ins, mods={"add(d)": "v + d"})
    read_out = pulse if cell == "pw" else freq
    mask = 0xFFF if cell == "pw" else 0xFFFF
    assert read_out(play(coded, 8)) == read_out(play(called, 8))
    assert read_out(play(coded, 8)) == [(k * d) & mask for k in range(1, 9)]


def _vibrato(template: str, head: str = "") -> str:
    ins = (
        f"{{ad: 9, sr: 240, {GATE}, base.freq: (φ < 1)*F(N) + (φ >= 1)*v," " wob.freq: vib(3,40,6)}"
    )
    slots = "slots: {freq: {base: {}, wob: {}}}\n"
    return doc(ins=ins, head=slots + head, mods={"vib(c, s, w)": template})


def test_a_goattracker_vibrato_on_a_state_byte_plays_as_its_expression():
    """t counts 0..w-1 and v rises by s for t < c, else falls."""
    coded = _vibrato(block(VIB), "state: {freq: {t: 1}}\n")
    expressed = _vibrato("v + s*(φ % w < c) - s*(φ % w >= c)")
    base = F(48)
    steps = [40, 40, 40, -40, -40, -40] * 2
    expected = [base + sum(steps[: k + 1]) for k in range(12)]
    assert freq(play(coded, 12)) == freq(play(expressed, 12)) == expected


def test_each_voice_has_its_own_state_kept_across_notes_and_bindings():
    """A note rebinds the slot (φ back at 0) but t counts on; voice 2 starts its own t."""
    ins = f"{{ad: 9, sr: 240, {GATE}, pw: cnt()}}"
    phrases = {"p1": "[C4.i1, E4.i1]", "p2": "[-, C4.i1]"}
    head = "voices: 2\ntempo: 2\nstate: {pw: {t: 1}}\n"
    frames = play(
        doc(phrases, ins=ins, head=head, rows=("[p1, p2]",), mods={"cnt()": block(COUNT)})
    )
    assert pulse(frames, 1) == [1, 2, 3, 4]
    assert pulse(frames, 2) == [0, 0, 1, 2]


def test_slots_of_a_cell_share_its_state_and_a_held_slot_runs_nothing():
    """Two counting slots add 2 a tick; b held on each step neither counts nor writes."""
    ins = f"{{ad: 9, sr: 240, {GATE}, a.pw: cnt(), b.pw: cnt()}}"
    head = "tempo: 3\nslots: {pw: {a: {}, b: {hold: [step]}}}\nstate: {pw: {t: 1}}\n"
    text = doc("[C4.i1, -]", ins=ins, head=head, mods={"cnt()": block(COUNT)})
    assert pulse(play(text)) == [1, 3, 5, 6, 8, 10]


def test_cut_has_one_state_for_the_song_whichever_voice_binds_it():
    """Voice 2's binding replaces voice 1's and counts on from its t."""
    phrases = {"p1": "[C4.i1!cut:cnt(), -*3]", "p2": "[-*2, C4.i1!cut:cnt(), -]"}
    head = "voices: 2\ntempo: 1\nstate: {cut: {t: 1}}\n"
    text = doc(phrases, head=head, rows=("[p1, p2]",), mods={"cnt()": block(COUNT)})
    assert cutoff(play(text)) == [1, 2, 3, 4]


def test_phi_and_the_notes_are_the_slot_s():
    """phi restarts on the slot's reset events; nn is N with the row's +12, ne the event note."""
    phi = "lda phi\nsta v\nlda phi+1\nsta v+1\nrts"
    ins = f"{{ad: 9, sr: 240, {GATE}, pw: ph()}}"
    head = "tempo: 3\nslots: {pw: {a: {reset: [step]}}}\n"
    text = doc("[C4.i1, -]", ins=ins, head=head, mods={"ph()": block(phi)})
    assert pulse(play(text)) == [0, 1, 2, 0, 1, 2]
    notes = "lda nn\nsta v\nlda ne\nsta v+1\nrts"
    ins = "{ad: 9, sr: 240, ctrl: [{ctrl: 65, note: +12}, end], freq: nt()}"
    text = doc("[C4.i1, -]", ins=ins, mods={"nt()": block(notes)})
    assert freq(play(text, 1)) == [48 << 8 | 60]


def test_bindings_by_a_slot_s_default_and_by_a_command_s_call():
    """A slot's default and a command each bind a template by its call."""
    head = "slots: {pw: {a: {mod: cnt()}, b: {}}}\nstate: {pw: {t: 1}}\n"
    text = doc(
        "[C4.i1!b.pw:add(100), -]",
        head=head,
        mods={"cnt()": block(COUNT), "add(d)": block(ADD)},
    )

    assert pulse(play(text, 3)) == [101, 102, 103]
    text = doc("[C4.i1!freq:nt(1,2), -]", mods={"nt(a1, b1)": "F(N + a1) + b1"})
    assert freq(play(text, 1)) == [F(49) + 2]


def test_a_call_keeps_phi_only_for_the_same_template_and_arguments():
    """Rebinding ``k(100)`` keeps φ; ``k(200)`` and ``j(100)``, the same body, restart it."""
    mods = {"k(b)": "b + 10*φ", "j(b)": "b + 10*φ"}
    text = doc(
        "[C4.i1!pw:k(100), -!pw:k(100), -!pw:k(200), -!pw:j(200)]",
        head="tempo: 2\n",
        mods=mods,
    )

    assert pulse(play(text)) == [100, 110, 120, 130, 200, 210, 200, 210]


def test_calls_bind_records_fields_rows_and_commands():
    """A pw record, an instrument's filter field, a program row and a command each take a call."""
    ins = (
        "{ad: 9, sr: 240, ctrl: [{ctrl: 65, note: +0}, {wait: 1, pw: walk(5)}, end],"
        " pw: pw1, filter: walk(7)}"
    )
    text = doc("[C4.i1, -]", ins=ins, pw={"pw1": "walk(1)"}, mods={"walk(s)": "v + s"})
    frames = play(text, 4)
    assert pulse(frames) == [1, 2, 7, 12]
    assert cutoff(frames) == [7, 14, 21, 28]


@pytest.mark.parametrize(
    "kwargs, ins, match",
    [
        ({"mods": {"m()": block("lda t\nsta v\nrts")}}, "pw: m()", "t is freq's state, not pw's"),
        ({"mods": {"m()": block("lda u\nsta v\nrts")}}, "pw: m()", "unknown symbol u"),
        ({"mods": {"m(d)": block(ADD)}}, "pw: m(1,2)", r"m\(1,2\): m takes 1 argument"),
        ({"mods": {"m(d)": block(ADD)}}, "pw: n(1)", "unknown modulator n"),
        ({"mods": {"m(d)": "v + d"}}, "pw: m", "a template is bound by a call"),
        ({"mods": {"m()": "v + 1"}}, "pw: 2*m", "a template is bound by a call"),
    ],
    ids=[
        "other-cell",
        "unknown",
        "arity",
        "undefined",
        "name",
        "in-expression",
    ],
)
def test_bindings_the_template_cannot_play_are_refused(kwargs, ins, match):
    head = "state: {freq: {t: 1}}\n"
    text = doc(ins=f"{{ad: 9, sr: 240, {GATE}, {ins}}}", head=head, **kwargs)
    with pytest.raises(PlayError, match=match):
        play(text, 2)


def test_a_slot_s_default_binds_a_template_by_a_call():
    text = doc(head="slots: {pw: {a: {mod: 'm'}}}\n", mods={"m()": "v + 1"})
    with pytest.raises(PlayError, match="a template is bound by a call"):
        play(text, 2)


CLOCK = "clock: {cycles_per_tick: 19656, clock_hz: 985000}\n"
TABLE = "ldx nn\nlda F.lo,x\nsta v\nlda F.hi,x\nsta v+1\nrts"


def test_code_reads_the_note_table_at_the_document_s_clock():
    """A routine reading F.lo and F.hi at nn plays F(N) at clock_hz, 0 where the word
    overflows, as the expression F(N) does."""
    phrase, head = "[C0.i1, A4.i1, C7.i1, B7.i1]", CLOCK + "tempo: 1\n"
    ins = f"{{ad: 9, sr: 240, {GATE}, freq: nt()}}"
    coded = doc(phrase, ins=ins, head=head, mods={"nt()": block(TABLE)})
    ins = f"{{ad: 9, sr: 240, {GATE}, freq: F(N)}}"
    expressed = doc(phrase, ins=ins, head=head)
    expected = [F(k, 985000) for k in (0, 57, 84, 95)]
    assert expected[-1] == 0 and expected[1] != F(57)
    assert freq(play(coded, 4)) == freq(play(expressed, 4)) == expected


def test_a_looping_song_with_a_code_modulator_needs_ticks(tmp_path):
    """Its loop is not proved: render and unitracker-sid refuse it without ticks."""
    text = doc(ins=f"{{ad: 9, sr: 240, {GATE}, pw: add(5)}}", ending=0, mods={"add(d)": block(ADD)})
    with pytest.raises(RenderError, match="--ticks"):
        render(read(text))
    rendered = render(read(text), 30)
    assert rendered.loop is None and (rendered.frames == play(text, 30)).all()
    path = tmp_path / "song.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        sid_main([str(path)])
    assert exc.value.code == 2
