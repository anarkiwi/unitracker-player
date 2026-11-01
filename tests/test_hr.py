"""Format 2.4's prelude hard restart: ``restart:hr(k)`` and ``hr(lead,ad,sr,stop)`` play as the
rows they stand for (docs/semantics.md, Hard restart)."""

import re

import numpy as np
import pytest
from song import doc, reg

from unitracker_player import play

CTRL = "ctrl:[{ctrl:65},{ctrl:33},{ctrl:17},{loop:0}]"
PHRASE = "[C4.i1,-*3,D,-*2,E,-,F,-*5]"


def written(restart: str, version: str = "2.3") -> str:
    """The document of an instrument whose restart is ``restart``."""
    text = doc(PHRASE, f"{{ad:9,sr:240,{CTRL},restart:{restart}}}", head="tempo:3\n")
    return re.sub(r"^unitracker:\S+", f"unitracker:{version}", text)


@pytest.mark.parametrize(
    "call, rows",
    [
        ("hr(2,15,0)", "[{hold:2,gate:0,ad:15,sr:0},{},end]"),
        ("hr(1,15,0)", "[{gate:0,ad:15,sr:0},{},end]"),
        ("hr(6,15,0)", "[{hold:6,gate:0,ad:15,sr:0},{},end]"),
        ("hr(2,15,0,1)", "[{hold:2,gate:0,ad:15,sr:0,run:ctrl[end]},{},end]"),
        ("hr(1,0,0,1)", "[{gate:0,ad:0,sr:0,run:ctrl[end]},{},end]"),
        ("hr(2)", "[{hold:2,gate:0,sr:'240 & v'!},{},end]"),
        ("hr(1)", "[{gate:0,sr:'240 & v'!},{},end]"),
        ("hr(3)", "[{hold:3,gate:0,sr:'240 & v'!},{},end]"),
    ],
)
def test_hr_plays_the_rows_it_stands_for(call, rows):
    frames = play(written(call, "2.4"), 80)
    np.testing.assert_array_equal(frames, play(written(rows, "2.5" if "v" in rows else "2.3"), 80))


def test_stop_changes_what_the_restart_plays():
    plain = reg(play(written("hr(2,15,0)", "2.4"), 80), 4)
    assert reg(play(written("hr(2,15,0,1)", "2.4"), 80), 4) != plain


@pytest.mark.parametrize("k", [1, 2, 3])
def test_hr_k_releases_the_gate_k_ticks_before_the_note_and_keeps_the_sustain(k):
    text = written(f"hr({k})", "2.4").replace("sr:240", "sr:167")
    frames = play(text, 12)
    gate, sr = reg(frames, 4), reg(frames, 6)
    off = [t for t in range(3, 12) if not gate[t] & 1]
    assert off == list(range(12 - k, 12))
    assert sr[12 - k] == 160 and sr[2] == 167 and sr[12 - k - 1] == 167


def test_a_document_s_own_hr_template_is_its_own():
    own = written("hr(2,15,0)").replace(
        "ins:\n", "restart:\n hr(h,a,s):[{hold:h,gate:0,ad:a,sr:s},{ctrl:9},end]\nins:\n"
    )
    np.testing.assert_array_equal(
        play(own, 80), play(written("[{hold:2,gate:0,ad:15,sr:0},{ctrl:9},end]"), 80)
    )
