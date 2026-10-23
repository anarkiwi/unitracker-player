"""The playroutine plays a rendered song's frames on the 6502, inside a frame's cycles, and the
.sid plays them on the sidtrace oracle."""

import struct

import numpy as np
import pytest
from conftest import FIXTURES, lifted
from song import doc

from unitracker_player import mos, read, sid
from unitracker_player.render import render

PAL_FRAME = 19656
ARP = "[{ctrl: 65, note: +0, pw: '16*abs(φ % 40 - 20) + 512'}, {wait: 1, note: +4}, {loop: 1}]"
FILT = "[{cut: 'v + 9', res: 15, route: 7, mode: 16, vol: 15}, end]"
SONGS = {
    "ending": doc(),
    "looping": doc(phrase="[C4.i1, E4*2, -*2]", ins=f"{{ad: 9, sr: 240, ctrl: {ARP}}}", ending=0),
    "restart": doc(
        phrase="[C4.i1, D4, -*2]",
        ins="{ad: 9, sr: 240, ctrl: [{ctrl: 65, note: +0}, end], restart: r1}",
        restart={"r1": "[{hold: 2, gate: 0, ad: 15, sr: 0}, {}, end]"},
        ending=0,
    ),
    "filter": doc(phrase="[C4.i1*9]", ins=f"{{ad: 9, ctrl: {ARP}, filter: {FILT}}}"),
    "voices": doc(
        phrase={"p1": "[C4.i1, -*3]", "p2": "[G3.i1*2, A3*2]"},
        rows=("[p1, p2]", "[p2, p1]"),
        head="voices: 2\n",
        ending=1,
    ),
}


def _simulated(tmp_path, rendered):
    plan = sid.plan(rendered)
    inc, song = sid.assembly(plan)
    period = 0 if rendered.loop is None else len(rendered.frames) - rendered.loop
    frames, cycles = mos.simulate(tmp_path, inc, song, len(rendered.frames) + period)
    expected = rendered.frames
    if period:
        expected = np.concatenate([expected, expected[rendered.loop :]])
    return frames, expected, cycles


@pytest.mark.usefixtures("toolchain")
@pytest.mark.parametrize("name", SONGS)
def test_the_playroutine_plays_the_rendered_frames(tmp_path, name):
    frames, expected, cycles = _simulated(tmp_path, render(read(SONGS[name])))
    assert np.array_equal(frames, expected)
    assert cycles.max() < PAL_FRAME


@pytest.mark.usefixtures("toolchain")
@pytest.mark.parametrize("name", FIXTURES)
def test_a_fixture_plays_on_the_6502_and_on_the_oracle(tmp_path, name):
    text = lifted(FIXTURES[name])
    rendered = render(read(text))
    frames, expected, cycles = _simulated(tmp_path, rendered)
    assert np.array_equal(frames, expected)
    assert cycles.max() < PAL_FRAME // 2
    survey = pytest.importorskip("pysidtracker.survey")
    testing = pytest.importorskip("pysidtracker.testing")
    data = sid.build(text)
    n = len(rendered.frames)
    _, grid, _ = survey.file_oracle(data, 1, n, work=testing.default_cache("csv"))
    assert np.array_equal(np.asarray(grid)[:n], rendered.frames)


def test_the_psid_header():
    data = sid.psid(b"\xea", {"title": "T", "author": "A" * 40}, 1022727, 0x1000)
    magic, version, offset, load, init, play, songs, start, speed = struct.unpack(
        ">4sHHHHHHHI", data[:22]
    )
    assert (magic, version, offset, load, init, play) == (b"PSID", 2, 0x7C, 0x1000, 0x1000, 0)
    assert (songs, start, speed) == (1, 1, 0)
    assert data[22:24] == b"T\0" and data[54:86] == b"A" * 32
    assert struct.unpack(">H", data[118:120])[0] == 2 << 2
    assert len(data) == 0x7C + 1 and data[-1] == 0xEA


def test_too_deep_a_grammar_is_refused(monkeypatch):
    monkeypatch.setattr(sid, "STACK", 0)
    with pytest.raises(sid.SidError):
        sid.plan(render(read(SONGS["looping"])))
