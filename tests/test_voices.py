"""Format 2.4's orderlists: each voice walks its own play entries and loop (docs/semantics.md,
Orderlists)."""

import math

import numpy as np
import pytest
from unitracker_grammar.document import printed, read

from unitracker_player import play
from unitracker_player.compiler import compile_document
from unitracker_player.document import read as read_document
from unitracker_player.render import render
from unitracker_player.sequencer import PlayError

DEFS = (
    "ins:\n i1:{ad:9,sr:240,ctrl:[{ctrl:65},{ctrl:64},end]}\n"
    "phrase:\n p1:[C4.i1,-]\n p2:[G4.i1,-]\n"
)
LOOPS = (3, 5, 7)


def text(head: str, song: str, version: str = "2.4", phrases: str = "") -> str:
    """The canonical document of ``head`` lines, the definitions, ``phrases`` and ``song``."""
    out = f"unitracker:{version}\nmeta:{{title:T}}\n{head}{DEFS}{phrases}song:\n{song}"
    assert printed(read(out)) == out
    return out


def entry(v: int, i: int) -> str:
    return f"p1+{10 * v + i + 1}"


def voiced(loops=LOOPS) -> str:
    lines = "".join(
        f" - voice:[p2,{','.join(entry(v, i) for i in range(n))}|loop:1]\n"
        for v, n in enumerate(loops)
    )
    return text(f"voices:{len(loops)}\ntempo:2\n", lines)


def test_three_voices_looping_apart_play_their_rows_written_to_the_common_multiple():
    lcm = math.lcm(*LOOPS)
    rows = [
        f" - play:[{','.join(entry(v, i % n) for v, n in enumerate(LOOPS))}]\n" for i in range(lcm)
    ]
    flat = text(
        "voices:3\ntempo:2\n", " - play:[p2,p2,p2]\n" + "".join(rows) + " - {loop:1}\n", "2.3"
    )
    ticks = 4 * (1 + 2 * lcm)
    np.testing.assert_array_equal(play(voiced(), ticks), play(flat, ticks))


def test_the_compiled_song_gives_each_voice_s_loop_entry_and_period():
    song = compile_document(read_document(voiced()))
    assert song.looping and song.periods == ((1, 4, 12), (1, 4, 20), (1, 4, 28))
    ended = voiced().replace("|loop:1]\n - voice:[p2,p1+11", "|end]\n - voice:[p2,p1+11", 1)
    assert compile_document(read_document(ended)).periods[0] is None


def test_a_looping_orderlist_song_renders_to_its_common_period():
    rendered = render(read_document(voiced()))
    assert len(rendered.frames) - rendered.loop == 4 * math.lcm(*LOOPS)


def test_each_voice_plays_its_own_phrases_whatever_the_others_lengths():
    """Phrases of 2 and 3 steps end apart: each voice plays as it would alone."""
    defs = " p3:[E4.i1,-,D]\n"
    both = text(
        "voices:2\ntempo:2\n",
        " - voice:[p1,p2|loop:0]\n - voice:[p3,p3+2,p2|loop:1]\n",
        "2.4",
        defs,
    )
    alone = [
        " - play:[p1]\n - play:[p2]\n - {loop:0}\n",
        " - play:[p3]\n - play:[p3+2]\n - play:[p2]\n - {loop:1}\n",
    ]
    frames = play(both, 60)
    for v, song in enumerate(alone):
        one = text("voices:1\ntempo:2\n", song, "2.3", defs)
        np.testing.assert_array_equal(frames[:, 7 * v : 7 * v + 7], play(one, 60)[:, :7])


def test_the_song_ends_when_every_voice_has_ended():
    ending = text("voices:2\ntempo:2\n", " - voice:[p1|end]\n - voice:[p1,p2,p1|end]\n")
    assert len(play(ending)) == 12
    with pytest.raises(PlayError, match="needs ticks"):
        play(voiced())


@pytest.mark.parametrize(
    "song, match",
    [
        (" - voice:[p1|end]\n", "1 orderlists for 2 voices"),
        (" - voice:[p1|end]\n - voice:[p1|loop:1]\n", "loop entry 1 out of range"),
    ],
)
def test_an_orderlist_per_voice_each_looping_to_one_of_its_entries(song, match):
    with pytest.raises(PlayError, match=match):
        play(text("voices:2\n", song), 1)
