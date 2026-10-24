"""A document with its definitions at their first use plays as with them in their sections."""

import numpy as np
import pytest
from conftest import FIXTURES, lifted
from test_inline import NAMED
from test_sid import SONGS
from unitracker_grammar.document import first_use, printed, read, top_level
from unitracker_player import play

DOCS = {name: lifted(name) for name in FIXTURES} | SONGS | NAMED


@pytest.mark.parametrize("name", DOCS)
def test_first_use_plays_as_the_sections(name):
    text = DOCS[name]
    moved = printed(first_use(read(text)))
    assert "=" in moved.split("song:")[1]
    assert printed(top_level(read(moved))).count("\n") == text.count("\n")
    assert np.array_equal(play(moved, 600), play(text, 600))
