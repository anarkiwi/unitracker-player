"""The instrument's modulator: its shape and bag over the note, and the note's !set writes."""

import pytest

from song import doc, freq, pulse
from unitracker_grammar import frequency as F
from unitracker_player import PlayError, play


def test_an_instrument_set_holds_its_offset_on_the_note():
    """mod: set {offset: 3} plays F(48) + 3 on every tick of the note."""
    assert freq(play(doc(ins="{ad: 9, sr: 240, mod: set {offset: 3}}"))) == [F(48) + 3] * 24


def test_an_instrument_ramp_steps_from_the_note_and_holds_its_skips():
    """mod: ramp {step: 2} plays F(48) + 2·t from the arm, held on the ticks of its skip."""
    ins = "{ad: 9, sr: 240, mod: ramp {step: 2}}"
    assert freq(play(doc(ins=ins))) == [F(48) + 2 * t for t in range(24)]
    held = freq(play(doc(ins="{ad: 9, sr: 240, mod: ramp {step: 2, skip: [1, 3]}}")))
    assert held[:8] == [F(48) + q for q in (0, 0, 2, 2, 4, 6, 8, 10)]


def test_a_note_set_writes_the_bag_of_its_own_arm():
    """A note's !set step 4 re-arms at its note and steps 4 from its own first tick."""
    text = doc(phrase="[E4.i1, E4.i1!set step 4]", ins="{ad: 9, sr: 240, mod: ramp {step: 2}}")
    assert freq(play(text)) == [F(52) + 2 * t for t in range(6)] + [F(52) + 4 * t for t in range(6)]


def test_a_detune_scales_the_note_base_by_the_voice_slot():
    """mod: set {detune: 1} plays the note base plus the voice's slot, on each of three voices."""
    text = doc(
        head="voices: 3\n",
        ins="{ad: 9, sr: 240, mod: set {detune: 1}}",
        phrase={"p1": "[C4.i1]", "p2": "[C4.i1]", "p3": "[C4.i1]"},
        rows=("[p1, p2, p3]",),
    )
    frames = play(text)
    assert [int(frames[0, 7 * slot]) for slot in range(3)] == [
        (F(48) + slot) & 255 for slot in range(3)
    ]


def test_a_note_frequency_clears_the_modulator_until_the_next_note():
    """A note's !freq:0 disarms the ramp; the next note re-arms it at its own base."""
    text = doc(phrase="[C4.i1!freq:0!legato, C4.i1]", ins="{ad: 9, sr: 240, mod: ramp {step: 2}}")
    assert freq(play(text)) == [F(48)] * 6 + [F(48) + 2 * t for t in range(6)]


def test_an_unknown_modulator_and_a_set_without_one_are_refused():
    with pytest.raises(PlayError, match="unknown modulator"):
        play(doc(ins="{ad: 9, sr: 240, mod: wobble {step: 2}}"))
    with pytest.raises(PlayError, match="no frequency modulator"):
        play(doc(phrase="[C4.i1!set step 4]", ins="{ad: 9, sr: 240}"))


def test_a_pulse_program_and_the_modulator_coexist():
    """The modulator shapes only the frequency: the instrument's pulse program still runs."""
    text = doc(ins="{ad: 9, sr: 240, pw: [{pw: 1000}, end], mod: ramp {step: 2}}")
    frames = play(text)
    assert freq(frames) == [F(48) + 2 * t for t in range(24)]
    assert pulse(frames) == [1000] * 24
