"""The player's register state per tick, derived by hand from docs/semantics.md."""

import pytest
from song import I1, cutoff, doc, freq, pulse, reg, timeline
from unitracker_grammar import frequency as F

from unitracker_player import Player, PlayError, load, play

ARP = "[{ctrl: 65, note: +0}, {wait: 1, note: +4}, {wait: 1, note: +7}, {loop: 1}]"
BARE = "{ad: 9}"


def test_a_note_sets_frequency_control_and_envelope():
    """C4 is note 48: $D400/$D401 hold F(48), the gate is on under ctrl 65, ad/sr are the
    instrument's and every other register stays 0 to the end of the phrase."""
    row = [F(48) & 255, F(48) >> 8, 0, 0, 65, 9, 240] + [0] * 18
    assert play(doc()).tolist() == [row] * 24


@pytest.mark.parametrize("entry, shift", [("p1", 0), ("p1+5", 5), ("p1-2", -2)])
def test_a_play_entry_transposes_its_phrase(entry, shift):
    """A transposed entry sounds F(48 + n), and the next row begins when the phrase ends."""
    frames = play(doc(rows=("[p1]", f"[{entry}]")))
    assert freq(frames) == [F(48)] * 24 + [F(48 + shift)] * 24


@pytest.mark.parametrize(
    "program, notes",
    [
        (ARP, [48, 48] + [52, 52, 55, 55] * 5 + [52, 52]),
        (
            "[{ctrl: 65, note: +0}, {wait: 2, hold: 3, note: +1}, {hold: 0, note: +2},"
            " {wait: 1, note: +3}, end]",
            [48] * 3 + [49] * 3 + [50] + [51] * 17,
        ),
        ("[{hold: 0, ctrl: 65, note: +5}, {note: +1}, end]", [49] * 24),
        (
            "[{hold: 2, ctrl: 65, note: +0}, {hold: 3, note: +12}, {loop: 0}]",
            [48 if t % 5 < 2 else 60 for t in range(24)],
        ),
        ("[{wait: 3, ctrl: 65, note: +2}, end]", [48] * 3 + [50] * 21),
        ("[{ctrl: 65, note: G#5}, {wait: 1, note: -2}, end]", [68, 68] + [46] * 22),
    ],
    ids=["arpeggio", "wait-hold", "hold-0", "loop-0", "first-wait", "absolute"],
)
def test_control_program_row_timing_and_notes(program, notes):
    """Row i+1 applies at row i's tick + hold_i + wait_(i+1); a relative note adds to the event
    note, an absolute one replaces it, and a loop's target row waits again."""
    frames = play(doc(ins=f"{{ad: 9, sr: 240, ctrl: {program}}}"))
    assert freq(frames) == [F(k) for k in notes]


def test_a_pulse_expression_wraps_to_12_bits():
    """An instrument pw expression fires each tick on the previous value: 1000, 2000, ... mod
    4096, split into $D402 and the low nibble of $D403."""
    frames = play(doc(ins="{pw: v + 1000}"))
    widths = [1000 * (t + 1) % 4096 for t in range(24)]
    assert widths[:5] == [1000, 2000, 3000, 4000, 904]
    assert reg(frames, 2) == [w & 255 for w in widths]
    assert reg(frames, 3) == [w >> 8 for w in widths]


@pytest.mark.parametrize("voice", [1, 2])
def test_a_filter_program_drives_the_shared_filter(voice):
    """The cutoff wraps to 11 bits and splits as cut & 7 / cut >> 3, `route_on: 0` sets the
    voice's bit beside the resonance and mode | vol fills $D418."""
    ins = "{filter: [{cut: v + 300, res: 9, route_on: 0, mode: 16, vol: 15}, end]}"
    phrases = {"p1": "[C4.i1, -*3]", "p2": "[-*4]"}
    rows = ("[p1, p2]" if voice == 1 else "[p2, p1]",)
    frames = play(doc(phrases, ins, rows, head="voices: 2\n"))
    cuts = [300 * (t + 1) % 2048 for t in range(24)]
    assert reg(frames, 0x15) == [c & 7 for c in cuts]
    assert reg(frames, 0x16) == [c >> 3 for c in cuts]
    assert reg(frames, 0x17) == [9 << 4 | 1 << (voice - 1)] * 24
    assert reg(frames, 0x18) == [16 | 15] * 24


def test_a_filter_program_a_voice_starts_stops_the_other_voices():
    """The filter runs one program: voice 2's note stops voice 1's, whose second row never
    applies."""
    ins = {"i1": "{filter: [{hold: 11, res: 1}, {res: 2}, end]}", "i2": "{filter: [{res: 5}, end]}"}
    phrases = {"p1": "[C4.i1, -*3]", "p2": "[-, C4.i2, -*2]"}
    frames = play(doc(phrases, ins, ("[p1, p2]",), head="voices: 2\n"))
    assert [r >> 4 for r in reg(frames, 0x17)] == [1] * 6 + [5] * 18


NOTES = [48, 50, 52, 53]


@pytest.mark.parametrize(
    "head, phrase, starts",
    [
        ("tempo: 3\n", "[C4.i1, D4, E4, F4]", [0, 3, 6, 9, 12]),
        ("tempo: [2, 4]\n", "[C4.i1, D4, E4, F4]", [0, 2, 6, 8, 12]),
        ("tempo: [2, 4]\n", "[C4.i1, D4!tempo:3, E4, F4]", [0, 2, 5, 8, 11]),
        ("", "[C4.i1!vtempo:2, D4, E4, F4]", [0, 2, 4, 6, 8]),
        ("", "[C4.i1, D4!vtempo:2, E4, F4]", [0, 6, 8, 10, 12]),
        ("tempo: [2, 4]\n", "[C4.i1!vtempo:3/5, D4, E4, F4]", [0, 3, 8, 11, 16]),
        ("init: [-!tempo:4]\n", "[C4.i1, D4, E4, F4]", [0, 4, 8, 12, 16]),
        ("", "[C4.i1*3, D4]", [0, 18, 24]),
        ("tempo: [2, 4]\n", "[C4.i1*2, D4, E4]", [0, 6, 8, 12]),
    ],
    ids=["tempo", "swing", "tempo-cmd", "vtempo", "vtempo-mid", "vswing", "init", "k", "k-swing"],
)
def test_step_boundaries(head, phrase, starts):
    """Steps alternate a and b, a tempo command fixes the step that begins at it (and !tempo
    returns the alternation to a), `*k` spans k alternating steps, and the song ends with the
    phrase."""
    frames = play(doc(phrase, head=head))
    assert freq(frames) == [F(k) for k in timeline(starts[:-1], NOTES, starts[-1])]


@pytest.mark.parametrize(
    "init, address, expected",
    [
        ("-!vol:15", 0x18, [15] * 24),
        ("-!res:2!route:5", 0x17, [2 << 4 | 5] * 24),
        ("E4.i1*3", 0, [F(52) & 255] * 24),
        ("E4.i1", 5, [9] * 24),
    ],
)
def test_the_init_row_applies_before_the_first_step(init, address, expected):
    """Init events act from tick 0 like events, with no duration of their own."""
    frames = play(doc("[-*4]", head=f"init: [{init}]\n"))
    assert reg(frames, address) == expected


def test_an_init_command_modulator_lasts_until_a_note_starts_an_instrument():
    """An init `!pw` binding fires from tick 0 until the first note, whose instrument's pulse
    program rebinds the slot."""
    ins = "{ad: 9, sr: 240, ctrl: [{ctrl: 65, note: +0}, end], pw: 50}"
    frames = play(doc("[-*2, C4.i1, -]", ins, head="init: [-!pw:'v + 1']\n"))
    assert pulse(frames) == [t + 1 for t in range(12)] + [50] * 12


@pytest.mark.parametrize(
    "restart, lead",
    [
        ("[{hold: 1, gate: 0, ad: 15, sr: 0}, {ctrl: 9}, end]", 1),
        ("[{hold: 2, gate: 0, ad: 15, sr: 0}, {ctrl: 9}, end]", 2),
        ("[{hold: 3, gate: 0, ad: 15, sr: 0}, {ctrl: 9}, end]", 3),
        ("[{hold: 2, gate: 0, ad: 15, sr: 0}, {wait: 1, ctrl: 9}, {loop: 0}]", 3),
        ("r1", 2),
    ],
)
def test_a_hard_restart_lands_before_the_note(restart, lead):
    """The restart's last row applies on the note's tick and sets the gate on with its ctrl;
    the row before applies hold + wait ticks earlier; a row before tick 0 is dropped."""
    ins = f"{{ad: 9, sr: 240, ctrl: [{{wait: 1, ctrl: 65, note: +0}}, end], restart: {restart}}}"
    r1 = {"r1": "[{hold: 2, gate: 0, ad: 15, sr: 0}, {ctrl: 9}, end]"}
    frames = play(doc("[C4.i1, D4, -*2]", ins, restart=r1))
    assert reg(frames, 4)[:12] == [9] + [65] * (5 - lead) + [64] * lead + [9] + [65] * 5
    assert reg(frames, 5)[:12] == [9] * (6 - lead) + [15] * lead + [9] * 6
    assert reg(frames, 6)[:12] == [240] * (6 - lead) + [0] * lead + [240] * 6


HR = "[{hold: 1, gate: 0, ad: 15, sr: 0}, {ctrl: 9}, end]"
LEAD = "{ad: 9, sr: 240, ctrl: [{ctrl: 65, note: +0}, end], restart: %s}"


@pytest.mark.parametrize(
    "phrase, ins, address, expected",
    [
        ("[C4.i1, off, -*2]", LEAD % HR, 4, [65] * 5 + [64] * 19),
        ("[C4.i1, off, -*2]", LEAD % HR, 5, [9] * 24),
        ("[C4.i1, off, -*2]", LEAD % HR, 6, [240] * 24),
        ("[C4.i1, off, on, -]", LEAD % HR, 4, [65] * 5 + [64] * 6 + [65] * 13),
        ("[C4.i1, -!sr:155, -*2]", LEAD % HR, 6, [240] * 5 + [155] * 19),
        ("[C4.i1, -!sr:155, -*2]", LEAD % HR, 5, [9] * 24),
        ("[C4.i1, -, -!sr:155, -]", LEAD % HR, 6, [240] * 11 + [155] * 13),
        ("[-.i1!sr:155, -*3]", LEAD % HR, 6, [155] * 24),
        ("[C4.i1, D4, -*2]", LEAD % HR, 4, [65] * 5 + [64] + [65] * 18),
        ("[C4.i1, D4, -*2]", LEAD % HR, 5, [9] * 5 + [15] + [9] * 18),
        (
            "[C4.i1, off, -*2]",
            LEAD % "[{hold: 2, gate: 0}, {ctrl: 9}, end]",
            4,
            [65] * 4 + [64] * 20,
        ),
        ("[C4.i1, off, -*2]", LEAD % "[{ctrl: 9}, end]", 4, [65] * 6 + [64] * 18),
        ("[C4.i1, off, -*2]", I1, 4, [65] * 6 + [64] * 18),
        ("[C4.i1, -!sr:155, -*2]", I1, 6, [240] * 6 + [155] * 18),
        ("[C4.i1, off.i2, -*2]", {"i1": LEAD % HR, "i2": I1}, 4, [65] * 6 + [64] * 18),
        ("[C4.i2, off.i1, -*2]", {"i1": LEAD % HR, "i2": I1}, 4, [65] * 6 + [64] * 18),
        ("[C4.i1, D4!legato, -*2]", LEAD % HR, 4, [65] * 24),
        ("[C4.i1, -!sr:155!legato, -*2]", LEAD % HR, 6, [240] * 5 + [155] * 19),
    ],
)
def test_an_event_applies_at_its_restart_lookahead(phrase, ins, address, expected):
    """A note's restart rows run before it and the note stays on its tick; an off, on or `-`
    with commands applies whole at the restart's first row's tick (tick 0 at the latest) and
    runs no row; without a lookahead row, without a restart and a `-` without commands the event
    stays on its step; a !legato event applies whole on its restart's last lookahead row's tick."""
    assert reg(play(doc(phrase, ins)), address) == expected


HR3 = "[{hold: 2, gate: 0}, {ctrl: 9}, {}, end]"


@pytest.mark.parametrize(
    "phrase, address, expected",
    [
        ("[C4.i1, off!sr:155, -*2]", 4, [65] * 3 + [64] * 21),
        ("[C4.i1, off!sr:155, -*2]", 6, [240] * 5 + [155] * 19),
        ("[C4.i1, -!sr:155, -*2]", 6, [240] * 5 + [155] * 19),
        ("[C4.i1, off, on!sr:155, -]", 4, [65] * 3 + [64] * 6 + [65] * 15),
        ("[C4.i1, off, on!sr:155, -]", 6, [240] * 11 + [155] * 13),
        ("[C4.i1, -!sr:155!vol:9, -*2]", 6, [240] * 5 + [155] * 19),
        ("[C4.i1, -!sr:155!vol:9, -*2]", 24, [0] * 6 + [9] * 18),
        ("[C4.i1, off!res:3!route:1, -*2]", 23, [0] * 6 + [0x31] * 18),
    ],
)
def test_a_note_less_event_splits_at_its_restart_lookahead(phrase, address, expected):
    """An off or on sets its gate on the restart's first lookahead row's tick and the event's
    voice commands apply on the last lookahead row's: here 3 and 1 ticks before the step; its
    filter and volume commands stay on the step."""
    assert reg(play(doc(phrase, LEAD % HR3)), address) == expected


def test_a_note_less_events_filter_program_starts_on_its_step():
    """A `-` event's !run of a filter program, as its other filter commands, applies on its step,
    not its restart's last lookahead row."""
    frames = play(doc("[C4.i1, -!run:f1, -*2]", LEAD % HR3, filter={"f1": "1024"}))
    assert cutoff(frames) == [0] * 6 + [1024] * 18


def test_a_restart_starts_on_its_steps_read_tick():
    """D4's restart, of one tick's lookahead, starts where the voice reads its step: three ticks
    before C4's restart's first-tick row, the lookahead of the instrument before it."""
    restart = "[{hold: %d, gate: 0, ad: 15, sr: 0}, {ctrl: 9}, {}, end]"
    ins = {"i1": LEAD % (restart % 3), "i2": LEAD % (restart % 1)}
    frames = play(doc("[C4.i1, D4.i2, -*2]", ins))
    assert reg(frames, 5)[:7] == [9, 9, 15, 15, 15, 15, 9]


FIRST_TICK = "[{hold: 2, gate: 0, ad: 15, sr: 0}, {ctrl: 9, gate: 1, ad: 9, sr: 240}, {}, end]"


@pytest.mark.parametrize(
    "phrase, restart, address, expected",
    [
        ("[C4.i1, D4, -*2]", FIRST_TICK, 5, [9] * 3 + [15] * 2 + [9] * 19),
        ("[C4.i1, D4!ad:10, -*2]", FIRST_TICK, 5, [9] * 3 + [15] * 2 + [10] * 19),
        ("[C4.i1, D4!sr:155, -*2]", FIRST_TICK, 6, [240] * 3 + [0] * 2 + [155] * 19),
        ("[C4.i1, D4!sr:155, -*2]", FIRST_TICK, 5, [9] * 3 + [15] * 2 + [9] * 19),
        ("[C4.i1, D4!ad:10!legato, -*2]", FIRST_TICK, 5, [9] * 5 + [10] * 19),
        ("[C4.i1!ad:10, -*3]", FIRST_TICK, 5, [10] * 24),
        ("[C4.i1, D4!ad:10, -*2]", HR, 5, [9] * 5 + [10] * 19),
        ("[C4.i1, D4!ad:10, -*2]", "[{ctrl: 9}, end]", 5, [9] * 6 + [10] * 18),
        ("[C4.i1, D4!ctrl:33, -*2]", FIRST_TICK, 4, [65] * 3 + [64] * 2 + [33] + [65] * 18),
    ],
)
def test_a_notes_envelope_commands_apply_with_its_restarts_first_tick_row(
    phrase, restart, address, expected
):
    """A note's !ad, !sr and !ctrl apply after its restart's last lookahead row, on that row's
    tick, winning over the row's own keys, and again on the note's tick after its instrument;
    its other commands, a dropped row and a restart of one row leave them on its tick, and a
    !legato note applies whole on that row's tick."""
    assert reg(play(doc(phrase, LEAD % restart)), address) == expected


def test_a_note_s_pulse_program_rebinds_after_its_command_and_another_note_leaves_it():
    """C4's pulse program row runs in the pulse phase, after the events, and rebinds the slot
    its command bound; a later command binds it, and D4's instrument, with no pulse program,
    leaves the binding and its phi."""
    ins = {"i1": "{ad: 9, sr: 240, ctrl: [{ctrl: 65, note: +0}, end], pw: 2000}", "i2": BARE}
    frames = play(doc("[C4.i1!pw:'1000 + phi', -!pw:'1000 + phi', D4.i2, -]", ins))
    assert pulse(frames) == [2000] * 6 + [1000 + t for t in range(18)]


def test_a_legato_note_applies_on_its_restarts_first_tick_row():
    """D4 !legato sets the note, and with it F(N), on the tick its restart's first-tick row
    would apply, a tick before its step."""
    assert freq(play(doc("[C4.i1, D4!legato, -*2]", LEAD % HR)))[3:8] == [F(48)] * 2 + [F(50)] * 3


def test_a_note_triggers_on_its_step_after_its_restart_lookahead():
    """The frequency and the instrument's envelope take the note on its step."""
    frames = play(doc("[C4.i1, D4, -*2]", LEAD % HR))
    assert freq(frames) == [F(48)] * 6 + [F(50)] * 18
    assert reg(frames, 6) == [240] * 5 + [0] + [240] * 18


@pytest.mark.parametrize(
    "second, gates", [("D4!legato", [65] * 3 + [64] * 21), ("D4", ([65] * 3 + [64] * 3) * 2)]
)
def test_legato_changes_only_the_event_note(second, gates):
    """With !legato the relative row note carries over to D4 and neither the gate nor the
    control program restarts; without it both do."""
    ins = "{ad: 9, sr: 240, ctrl: [{ctrl: 65, note: +3}, {wait: 2, gate: 0}, end]}"
    frames = play(doc(f"[C4.i1, {second}, -*2]", ins))
    assert freq(frames) == [F(51)] * 6 + [F(53)] * 18
    assert reg(frames, 4)[: len(gates)] == gates


R24 = range(24)
CASES = {
    "freq-command-outlasts-a-note": (
        doc(phrase="[C4.i1!freq:'F(N) + 3*phi', -, D4, -]"),
        freq,
        [F(48 if t < 12 else 50) + 3 * t for t in R24],
    ),
    "freq-offset-none": (
        doc(phrase="[C4.i1!freq:+5, -!freq:none, -*2]"),
        freq,
        [F(48) + 5] * 6 + [F(48)] * 18,
    ),
    "freq-negative": (doc(phrase="[C4.i1!freq:-3, -*3]"), freq, [F(48) - 3] * 24),
    "freq-int": (doc(phrase="[C4.i1!freq:7, -*3]"), freq, [F(48) + 7] * 24),
    "freq-command-rebinds-the-instrument-s-slot": (
        doc(ins="{freq: F(N) + phi}", phrase="[C4.i1, -!freq:+0, -!freq:none, -]"),
        freq,
        [F(48) + (t if t < 6 else 0) for t in R24],
    ),
    "freq-hold-keeps-a-slide": (
        doc(phrase="[C4.i1!freq:'F(N) + phi', -!freq:hold, -*2]"),
        freq,
        [F(48) + min(t, 5) for t in R24],
    ),
    "freq-hold-ends-the-program-modulator": (
        doc(ins="{freq: F(N) + phi}", phrase="[C4.i1, -!freq:hold, -*2]"),
        freq,
        [F(48) + min(t, 5) for t in R24],
    ),
    "freq-hold-ends-at-note": (
        doc(phrase="[C4.i1!freq:+5, -!freq:hold, D4, -]"),
        freq,
        [F(48) + 5] * 12 + [F(50)] * 12,
    ),
    "freq-hold-none": (
        doc(phrase="[C4.i1!freq:+5, -!freq:hold, -!freq:none, -]"),
        freq,
        [F(48) + 5] * 12 + [F(48)] * 12,
    ),
    "freq-hold-yields-to-a-row": (
        doc(
            ins="{ctrl: [{ctrl: 65}, {wait: 9, freq: F(N) + 50}, end]}",
            phrase="[C4.i1!freq:+5, -!freq:hold, -*2]",
        ),
        freq,
        [F(48) + (5 if t < 10 else 50) for t in R24],
    ),
    "freq-row-none-returns-to-the-note": (
        doc(ins="{ctrl: [{ctrl: 65, freq: F(N) + phi}, {wait: 2, freq: none}, end]}"),
        freq,
        [F(48) + (t if t < 3 else 0) for t in R24],
    ),
    "freq-row-hold": (
        doc(ins="{ctrl: [{ctrl: 65, freq: F(N) + phi}, {wait: 2, freq: hold}, end]}"),
        freq,
        [F(48) + min(t, 2) for t in R24],
    ),
    "freq-wraps-16-bits": (doc(ins="{freq: F(N) + 65539}"), freq, [F(48) + 3] * 24),
    "pw-command-persists": (
        doc(phrase="[C4.i1, -!pw:'v + 100', -, D4]"),
        pulse,
        [0 if t < 6 else 100 * (t - 5) for t in R24],
    ),
    "pw-command-rebinds": (
        doc(ins="{pw: v + 1000}", phrase="[C4.i1, -!pw:7, D4, -]"),
        pulse,
        [
            1000 * (t + 1) % 4096 if t < 6 else 7 if t < 12 else (7 + 1000 * (t - 11)) % 4096
            for t in R24
        ],
    ),
    "pw-hold": (
        doc(ins="{pw: v + 100}", phrase="[C4.i1, -!pw:hold, -*2]"),
        pulse,
        [100 * (min(t, 5) + 1) for t in R24],
    ),
    "pw-program-row-rebinds-after-the-command-and-none-ends-it": (
        doc(ins="{pw: v + 100}", phrase="[C4.i1!pw:7, -!pw:none, -*2]"),
        pulse,
        [100 * (min(t, 5) + 1) for t in R24],
    ),
    "pw-keep": (
        doc(ins="{pw: 100*phi + 1}", phrase="[C4.i1, D4!pw, -*2]"),
        pulse,
        [100 * t + 1 for t in R24],
    ),
    "pw-restart": (
        doc(ins="{pw: 100*phi + 1}", phrase="[C4.i1, D4, -*2]"),
        pulse,
        [100 * (t if t < 6 else t - 6) + 1 for t in R24],
    ),
    "pw-row-hold": (
        doc(ins="{pw: [{pw: v + 100}, {wait: 2, pw: hold}, {wait: 2, pw: v + 1}, end]}"),
        pulse,
        [100 * (min(t, 2) + 1) for t in R24],
    ),
    "pw-row-none-keeps-the-slot": (
        doc(ins="{pw: [{pw: v + 100}, {wait: 2, pw: none}, {wait: 2, pw: v + 1}, end]}"),
        pulse,
        [100 * (min(t, 2) + 1) + max(t - 5, 0) for t in R24],
    ),
    "pw-from-ctrl-row": (
        doc(ins="{ctrl: [{ctrl: 65, pw: 100*phi + 50}, end]}"),
        pulse,
        [100 * t + 50 for t in R24],
    ),
    "pw-run-from-ctrl": (
        doc(ins="{ctrl: [{ctrl: 65, run: pw1}, end]}", pw={"pw1": "v + 1000"}),
        pulse,
        [1000 * (t + 1) % 4096 for t in R24],
    ),
    "pw-run-command": (
        doc(phrase="[C4.i1!run:pw1, -*3]", pw={"pw1": "v + 1000"}),
        pulse,
        [1000 * (t + 1) % 4096 for t in R24],
    ),
    "pw-constant": (doc(phrase="[C4.i1!pw:2048, -*3]"), pulse, [2048] * 24),
    "cut-wraps-11-bits": (
        doc(ins="{filter: v + 1000}"),
        cutoff,
        [1000 * (t + 1) % 2048 for t in R24],
    ),
    "cut-row-hold": (
        doc(ins="{filter: [{cut: v + 100}, {wait: 2, cut: hold}, {wait: 2, cut: v + 1}, end]}"),
        cutoff,
        [100 * (min(t, 2) + 1) for t in R24],
    ),
    "cut-row-none-keeps-the-slot": (
        doc(ins="{filter: [{cut: v + 100}, {wait: 2, cut: none}, {wait: 2, cut: v + 1}, end]}"),
        cutoff,
        [100 * (min(t, 2) + 1) + max(t - 5, 0) for t in R24],
    ),
    "cut-command-hold": (
        doc(ins="{filter: v + 100}", phrase="[C4.i1, -!cut:hold, -*2]"),
        cutoff,
        [100 * (min(t, 5) + 1) for t in R24],
    ),
    "cut-program-row-rebinds-after-the-command-and-none-ends-it": (
        doc(ins="{filter: v + 100}", phrase="[C4.i1!cut:7, -!cut:none, -*2]"),
        cutoff,
        [100 * (min(t, 5) + 1) for t in R24],
    ),
    "cut-command-outlasts-a-note": (
        doc(phrase="[C4.i1!cut:'v + 50', -, D4, -]"),
        cutoff,
        [50 * (t + 1) for t in R24],
    ),
    "cut-command-hold-keeps-its-value": (
        doc(phrase="[C4.i1!cut:'v + 100', -!cut:hold, -*2]"),
        cutoff,
        [100 * (min(t, 5) + 1) for t in R24],
    ),
    "cut-constant": (doc(phrase="[-!cut:1000, -*3]"), cutoff, [1000] * 24),
    "cut-keep": (
        doc(ins="{filter: 8*phi + 8}", phrase="[C4.i1, D4!filter, -*2]"),
        cutoff,
        [8 * t + 8 for t in R24],
    ),
    "cut-restart": (
        doc(ins="{filter: 8*phi + 8}", phrase="[C4.i1, D4, -*2]"),
        cutoff,
        [8 * (t if t < 6 else t - 6) + 8 for t in R24],
    ),
    "filter-by-name": (
        doc(ins="{filter: f1}", filter={"f1": "1024"}),
        cutoff,
        [1024] * 24,
    ),
    "ctrl-mask": (
        doc(ins="{ctrl: [{ctrl: 65, mask: 8}, {ctrl: 17}, {mask: 1}, end]}"),
        lambda f: reg(f, 4),
        [(65 ^ 8) & 0xFE | 1, (17 ^ 8) & 0xFE | 1] + [17] * 22,
    ),
    "gate-row": (
        doc(ins="{ctrl: [{ctrl: 65}, {gate: 0}, {gate: 2}, end]}"),
        lambda f: reg(f, 4),
        [65, 64] + [65] * 22,
    ),
    "gate-atoms": (
        doc(phrase="[C4.i1, off, on, -]"),
        lambda f: reg(f, 4),
        timeline([0, 6, 12], [65, 64, 65], 24),
    ),
    "ctrl-by-name": (
        doc(ins="{ctrl: c1}", ctrl={"c1": "[{ctrl: 33}, end]"}),
        lambda f: reg(f, 4),
        [33] * 24,
    ),
    "run-ctrl-from-filter": (
        doc(ins="{filter: [{run: c1}, end]}", ctrl={"c1": "[{ctrl: 65}, end]"}),
        lambda f: reg(f, 4),
        [0] + [65] * 23,
    ),
    "run-none": (
        doc(ins=BARE, phrase="[C4.i1!run:c1, -!run:none, -*2]", ctrl={"c1": ARP}),
        freq,
        [F(k) for k in [48, 48, 52, 52, 55, 55] + [55] * 18],
    ),
    "run-f1-command": (
        doc(phrase="[C4.i1!run:f1, -*3]", filter={"f1": "7"}),
        cutoff,
        [7] * 24,
    ),
    "instrument-only": (
        doc(ins={"i1": I1, "i2": "{ad: 7}"}, phrase="[C4.i1, -.i2, D4, -]"),
        lambda f: reg(f, 5) + reg(f, 6),
        [9] * 12 + [7] * 12 + [240] * 24,
    ),
    "freq-row-int": (
        doc(ins="{ctrl: [{ctrl: 65, note: +0, freq: 5}, end]}"),
        freq,
        [F(48) + 5] * 24,
    ),
    "freq-row-expr": (
        doc(ins="{ctrl: [{ctrl: 65, note: +0, freq: F(N) + 2*phi}, end]}"),
        freq,
        [F(48) + 2 * t for t in R24],
    ),
    "route": (
        doc(
            ins="{filter: [{route: 7}, {route_off: 2}, {route_on: 8}, {route_off: 0},"
            " {route: 31}, end]}"
        ),
        lambda f: reg(f, 0x17)[:5],
        [7, 4, 13, 12, 15],
    ),
    "res-low-bits": (
        doc(ins="{filter: [{res: 31, mode: 255, vol: 31}, end]}"),
        lambda f: reg(f, 0x17)[:1] + reg(f, 0x18)[:1],
        [15 << 4, 0xF0 | 15],
    ),
    "envelope-rows": (
        doc(ins="{ctrl: [{ctrl: 65, ad: 3, sr: 4}, end]}"),
        lambda f: reg(f, 5)[:1] + reg(f, 6)[:1],
        [3, 4],
    ),
    "commands": (
        doc(ins=BARE, phrase="[C4.i1!ad:1!sr:2!ctrl:17!res:15!route:3!mode:32!vol:7, -*3]"),
        lambda f: [reg(f, a)[0] for a in (4, 5, 6, 0x17, 0x18)],
        [17, 1, 2, 0xF3, 32 | 7],
    ),
    "ctrl-gate-off": (
        doc(ins=BARE, phrase="[off.i1!ctrl:17, -*3]"),
        lambda f: reg(f, 4)[:1],
        [16],
    ),
    "clock": (
        doc(head="clock: {cycles_per_tick: 17095, clock_hz: 1022727}\n"),
        freq,
        [F(48, 1022727)] * 24,
    ),
}


@pytest.mark.parametrize("text, read, expected", CASES.values(), ids=CASES.keys())
def test_cells_rows_and_commands(text, read, expected):
    """Each row key, command and modulator ownership rule on its cell's registers."""
    frames = play(text)
    assert read(frames) == expected


@pytest.mark.parametrize(
    "expr, value",
    [
        ("7 // 2 + 7 % 3 + (5 > 3) + abs(-9) + min(4, 8) + max(4, 8)", 26),
        ("5 // 0 + 5 % 0 + (2 == 3) + (2 != 3) + (2 <= 3) + (2 >= 3) + (2 < 3)", 3),
        ("-7 // 2 + 20", 16),
        ("-7 % 3", 2),
        ("-(3 - 5)*4", 8),
        ("F(-1) + F(1000) + 1", 1),
        ("F(N) - F(48) + N", 48),
        ("W[1] + W[9] + W[-1]", 6),
        ("v - 1", 4095),
    ],
)
def test_expressions_are_floored_python_integers(expr, value):
    """`//` and `%` floor and give 0 for a 0 divisor, comparisons are 1 or 0, F is 0 outside its
    table, T[k] is 0 outside the list and the result wraps to the cell."""
    text = doc(f"[C4.i1!pw:'{expr}', -*3]", head="tables:\n  W: [5, 6, 7]\n")
    assert pulse(play(text))[0] == value


def test_voices_continue_end_and_start_rows_on_their_own_phrases():
    """A row starts when the shortest playing phrase ends; `~` keeps the other voice playing
    and `end` stops a voice whose last note holds."""
    phrases = {"p1": "[C4.i1, -*3]", "p2": "[E4.i1, -]"}
    frames = play(doc(phrases, rows=("[p1, p2]", "[~, p2+2]"), head="voices: 2\n"))
    assert freq(frames, 1) == [F(48)] * 24
    assert freq(frames, 2) == [F(52)] * 12 + [F(54)] * 12
    frames = play(doc(phrases, rows=("[p1, p2]", "[~, end]"), head="voices: 2\n"))
    assert len(frames) == 24 and freq(frames, 2) == [F(52)] * 24


def test_an_ending_song_plays_to_its_end_and_a_looping_one_repeats():
    """`end` stops the song after its last row; `{loop: k}` continues at row k and needs
    ticks; ticks cut either short."""
    phrases = {"p1": "[C4.i1, -*3]", "p2": "[E4.i1, -]"}
    assert len(play(doc(phrases, rows=("[p1]", "[p2]")))) == 36
    assert len(play(doc(), 10)) == 10
    assert len(play(doc(rows=("[p1]", "[end]")))) == 24
    assert len(play(load(doc()), 100)) == 24
    looping = doc(phrases, rows=("[p1]", "[p2]"), ending=1)
    assert freq(play(looping, 60)) == [F(48)] * 24 + [F(52)] * 36
    assert freq(play(doc(ending=0), 50)) == [F(48)] * 50
    with pytest.raises(PlayError, match="needs ticks"):
        play(looping)


def test_the_player_steps_until_the_song_ends():
    """`Player.step` gives the 25 registers of each tick, then None."""
    player = Player(load(doc()))
    regs = [player.step() for _ in range(25)]
    assert regs[0][:7] == [F(48) & 255, F(48) >> 8, 0, 0, 65, 9, 240] and regs[23] == regs[0]
    assert regs[24] is None and player.step() is None


def test_regs_is_the_register_file_each_tick_rewrites():
    """`Player.regs` holds the last tick's registers; each step returns a copy of it."""
    player = Player(load(doc()))
    first = player.step()
    assert first == player.regs and first is not player.regs
    player.voices[0].ad = 0x5A
    after = player.registers()
    assert [i for i, (x, y) in enumerate(zip(first, after)) if x != y] == [5]
    assert after[5] == player.regs[5] == 0x5A and first[5] == 9


def _twice(text: str) -> str:
    return text.replace("ins:\n", "ins:\n  i1: {ad: 1}\n", 1)


@pytest.mark.parametrize(
    "text, match",
    [
        (doc(head="voices: 4\n", rows=("[p1, p1, p1, p1]",)), "voices"),
        (doc(head="voices: 2\n"), "play entries"),
        (doc(head="init: [-, -]\n"), "init"),
        (doc(head="tempo: 0\n"), "tempo"),
        (doc(head="tempo: [3, 0]\n"), "tempo"),
        (doc("[C4.i1!tempo:0, -*3]"), "tempo"),
        (doc(ending=1), "loop"),
        (doc(ins="{ctrl: [{ctrl: 65}, {loop: 3}]}"), "loop"),
        (doc("[C4.i2, -*3]"), "i2"),
        (doc("[C4.i1!pw:'W[0]', -*3]"), "W"),
        (doc(ins="{ctrl: [{hold: 0, ctrl: 65}, {loop: 0}]}"), "loops in no time"),
        (
            doc(
                {"p1": "[C4.i1, -*3]", "p2": "[E4.i1, -]"},
                rows=("[p1, p2]", "[~, ~]"),
                head="voices: 2\n",
            ),
            "voice 2",
        ),
        (
            doc(
                {"p1": "[C4.i1, -*3]", "p2": "[E4.i1, -]"},
                rows=("[p1, p2]", "[p1, p2]"),
                head="voices: 2\n",
            ),
            "voice 1",
        ),
        (_twice(doc()), "twice"),
        (doc(phrase="[-!run:c1, C4.i1, -*2]", ctrl={"c1": ARP}), "no instrument"),
    ],
    ids=[
        "voices",
        "entries",
        "init",
        "tempo",
        "swing-0",
        "tempo-cmd",
        "song-loop",
        "program-loop",
        "name",
        "table",
        "no-time",
        "continues-ended",
        "restarts-playing",
        "twice",
        "run-no-instrument",
    ],
)
def test_refusals(text, match):
    """More than 3 voices, a play row or init row of the wrong length, a tempo
    of 0, a loop outside its rows, an undefined name or table, a program looping in no time, an
    entry that disagrees with which phrases ended, a name defined twice and a program run on a
    voice with no instrument are refused."""
    with pytest.raises(ValueError, match=match):
        play(text, 100)


def test_a_split_event_keeps_its_compiled_event_in_both_parts():
    """A subclass sees the phrase's one compiled event at both of its parts' ticks."""
    seen = []

    class Recorder(Player):
        """Records each event part it plays."""

        def _event(self, t, v, event, *rest):  # pylint: disable=arguments-differ
            seen.append((t, event, rest[-1]))
            super()._event(t, v, event, *rest)

    song = load(doc("[C4.i1, off!sr:155, -*2]", LEAD % HR3))
    player = Recorder(song)
    for _ in range(8):
        player.step()
    off = song.timeline[0][0][1][1]
    assert [(t, e is off, p) for t, e, p in seen] == [
        (0, False, None),
        (3, True, "gate"),
        (5, True, "ops"),
    ]
