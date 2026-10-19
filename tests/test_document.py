"""Reading a canonical document's header and refusing other spellings."""

import pytest
from song import doc
from unitracker_grammar import CanonicalError

from unitracker_player import load, play, read

HEAD = (
    "clock: {cycles_per_tick: 17095, clock_hz: 1022727}\nvoices: 2\ntempo: [6, 7]\n"
    "tables:\n  W: [1, 2, 3]\n"
)


def test_the_header_is_read_with_its_values():
    """Meta text in its three spellings, the clock, voices, swing tempo and tables."""
    text = doc(head=HEAD, rows=("[p1, p1]",)).replace(
        "meta: {title: T}", "meta: {title: 'It''s', author: \"A \\\"B\\\"\", released: 1987}"
    )
    header = read(text).header
    assert header.meta == {"title": "It's", "author": 'A "B"', "released": "1987"}
    assert (header.cycles_per_tick, header.clock_hz) == (17095, 1022727)
    assert (header.voices, header.tempo, header.version) == (2, (6, 7), "1.20")
    assert read(text).tables == {"W": (1, 2, 3)}


@pytest.mark.parametrize(
    "old, new",
    [
        ("{ad: 9, sr: 240,", "{sr: 240, ad: 9,"),
        ("[C4.i1, -*3]", "[C4 .i1, -*3]"),
        ("  - end", "  - loop: 0"),
        ("meta: {title: T}\n", ""),
    ],
)
def test_other_spellings_are_canonical_errors(old, new):
    """Key order, a space in an event, the ending's form and a missing meta are grammar
    errors with their position."""
    text = doc().replace(old, new)
    with pytest.raises(CanonicalError, match=r"line \d+ column \d+"):
        read(text)
    with pytest.raises(CanonicalError):
        play(load(text))
