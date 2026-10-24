"""A rendered song is play()'s frames, and a looping one provably repeats from its loop."""

import numpy as np
import pytest
from song import doc

from unitracker_player import play, read
from unitracker_player.render import RenderError, render

PW = "[{ctrl: 65, note: +0, pw: '16*abs(φ % 40 - 20) + 512'}, end]"
VIB = "[{ctrl: 65, note: +0, freq: 'F(N) + 8*(φ % 6 < 3) - 4'}, end]"


def test_an_ending_song_renders_to_its_end():
    text = doc()
    r = render(read(text))
    assert r.loop is None and np.array_equal(r.frames, play(text))


@pytest.mark.parametrize("ctrl", [PW, VIB])
@pytest.mark.parametrize("phrase", ["[C4.i1, -*3]", "[C4.i1*7]", "[C4.i1, D4*2, -*2]"])
def test_a_looping_song_repeats_from_its_loop(ctrl, phrase):
    text = doc(phrase=phrase, ins=f"{{ad: 9, sr: 240, ctrl: {ctrl}}}", ending=0)
    r = render(read(text))
    assert r.loop is not None
    period = len(r.frames) - r.loop
    ticks = len(r.frames) + 5 * period
    played = play(text, ticks)
    index = [t if t < len(r.frames) else r.loop + (t - r.loop) % period for t in range(ticks)]
    assert np.array_equal(played, r.frames[index])


def test_a_modulator_whose_phase_never_recurs_is_refused():
    text = doc(phrase="[-]", ins="{ad: 9}", head="init: [-!pw:'φ']\n", ending=0)
    with pytest.raises(RenderError):
        render(read(text))


def test_ticks_render_a_fixed_length():
    text = doc(ending=0)
    r = render(read(text), ticks=50)
    assert r.loop is None and np.array_equal(r.frames, play(text, 50))
