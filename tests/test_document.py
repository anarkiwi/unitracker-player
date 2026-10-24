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
    document = read(text)
    header = document.header
    assert header.meta == {"title": "It's", "author": 'A "B"', "released": "1987"}
    assert (header.cycles_per_tick, header.clock_hz) == (17095, 1022727)
    assert (header.voices, header.tempo, header.slots) == (2, (6, 7), {})
    assert document.tables == {"W": (1, 2, 3)}


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


def _edit(text: str, *pairs) -> str:
    for old, new in pairs:
        assert old in text
        text = text.replace(old, new, 1)
    return text


PW = doc("[C4.i1!run:pw1, -*3]", pw={"pw1": "v + 3"})
REMOVED = {
    "version-1.23": _edit(doc(), ("unitracker: 2.1", "unitracker: 1.23")),
    "version-2.0": _edit(doc(), ("unitracker: 2.1", "unitracker: 2.0")),
    "hold-line": _edit(doc(), ("voices: 1\n", "voices: 1\nhold: {pw: [step]}\n")),
    "instance": _edit(doc("[C4.i1!pw:1, -*3]"), ("!pw:1", "!pw:x1")),
    "set": _edit(doc("[C4.i1!pw:1, -*3]"), ("!pw:1", "!set:pw:1")),
    "command-filter-none": _edit(doc("[C4.i1!cut:none, -*3]"), ("!cut:none", "!filter:none")),
    "row-filter-none": _edit(
        doc(ins="{filter: [{cut: none}, end]}"), ("cut: none", "filter: none")
    ),
    "bag": _edit(doc(ins="{pw: v + 1}"), ("pw: v + 1", "pw: up {s: 1}")),
    "shape": _edit(doc(ins="{pw: v + 1}"), ("pw: v + 1", "mod: ramp {step: 5}")),
    "name-keyed-template": _edit(doc(mods={"up()": "v + 1"}), ("up(): v + 1", "up: v + 1")),
    "kind-prefixed-run": _edit(PW, ("!run:pw1", "!run:pw:pw1")),
    "row-phrases": _edit(
        PW,
        (
            "phrase:\n  p1: [C4.i1!run:pw1, -*3]\nsong:\n  - play: [p1]",
            "song:\n  - def:\n      phrase:\n        p1: [C4.i1!run:pw1, -*3]\n    play: [p1]",
        ),
    ),
    "phi": _edit(doc(ins="{pw: v + phi}"), ("v + phi", "v + φ")),
    "resfilt": _edit(doc("[C4.i1!res:1!route:2, -*3]"), ("!res:1!route:2", "!resfilt:1/2")),
    "ctrl-mask": _edit(doc(ins="{ctrl: [{ctrl: 65, mask: 8}, end]}"), ("65, mask: 8", "65^8")),
    "route-add": _edit(doc(ins="{ctrl: [{route_on: 0}, end]}"), ("route_on: 0", "route: +")),
    "pw-program-of-one-binding": _edit(PW, ("pw1: v + 3", "pw1: [{pw: v + 3}, end]")),
}


@pytest.mark.parametrize("text", REMOVED.values(), ids=REMOVED.keys())
def test_removed_constructs_are_canonical_errors(text):
    """Another version and the spellings formats 2.0 and 2.1 dropped are outside the grammar."""
    with pytest.raises(CanonicalError):
        read(text)
