"""An ``env`` writes a voice's envelope registers as segments within one call
(docs/semantics.md, Env)."""

import numpy as np
import pytest
from song import doc, reg
from unitracker_grammar import CanonicalError, parse

from unitracker_player import Player, PlayError, load, play

AD, SR, CTRL = 5, 6, 4
I1 = "{ad:9,sr:240,ctrl:[{ctrl:65,note:+0},end]}"


def played(phrase: str, ins: str = I1, frames: int = 6) -> np.ndarray:
    return play(doc(phrase, ins, head="tempo:1\n"), frames)


def segments(phrase: str, ins: str = I1, frames: int = 6) -> list:
    """Voice 1's segments of each frame."""
    player = Player(load(doc(phrase, ins, head="tempo:1\n")))
    out = []
    for _ in range(frames):
        player.step()
        out.append(player.segments[0])
    return out


@pytest.mark.parametrize(
    "sugar, env",
    [
        (" ctrl:9", " env:_._.9"),
        (" ad:3 sr:7", " env:7.3.65"),
        (" ad:1 ctrl:64", " env:_.1.64"),
        (" ctrl:65", " env:_._.65"),
    ],
)
def test_one_segment_is_the_plain_cells(sugar, env):
    phrase = "[C4.i1 ctrl:65,-,D{},-,-]"
    plain = "{ad:9,sr:240}"
    np.testing.assert_array_equal(
        played(phrase.format(sugar), plain), played(phrase.format(env), plain)
    )


def test_segments_are_written_in_order_and_the_last_is_what_the_frame_holds():
    frames = played("[C4.i1,-,D env:_._.64/15.1.65,-,-]")
    assert (reg(frames, CTRL)[2], reg(frames, AD)[2], reg(frames, SR)[2]) == (65, 1, 15)
    assert segments("[C4.i1,-,D env:_._.64/15.1.65,-,-]")[2] == [(240, 9, 64), (15, 1, 65)]


def test_held_values_are_the_call_s_and_the_cells_keep_the_last_segment():
    out = segments("[C4.i1,-,D env:_._.64/_.7.65,-,-]")
    assert out[1] == [(240, 9, 65)]
    assert out[2] == [(240, 9, 64), (240, 7, 65)]
    assert out[3] == [(240, 7, 65)]


def test_an_env_takes_a_gate_off_across_calls():
    frames = played("[C4.i1,-,D env:0.0.64,-,-]")
    assert reg(frames, CTRL)[2] == 64 and reg(frames, SR)[2] == 0


def test_a_segment_ends_at_a_gate_or_test_edge():
    with pytest.raises(PlayError, match="edge"):
        played("[C4.i1,D env:_._.64/_._.64]")
    played("[C4.i1,D env:_._.64/_._.72]")


def test_an_env_is_format_2_5():
    text = doc("[C4.i1,D env:_._.64]").replace("unitracker:2.5", "unitracker:2.3")
    with pytest.raises(CanonicalError):
        parse(text)


def test_a_program_row_writes_an_env():
    ins = "{ad:9,sr:240,ctrl:[{ctrl:65},{env:_._.64/_._.65},{env:_._.64/_._.65},end]}"
    out = segments("[C4.i1,-,-]", ins, 4)
    assert out[1] == [(240, 9, 64), (240, 9, 65)] and out[2] == out[1]
