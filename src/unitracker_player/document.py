"""Reading a canonical document: unitracker-grammar's model of its text, in format 2.3's core."""

import json
from collections import Counter

from unitracker_grammar import CanonicalError
from unitracker_grammar.document import Document, top_level
from unitracker_grammar.document import read as _read
from unitracker_grammar.expand import expanded

__all__ = ["Document", "meta_texts", "read"]


def read(text: str) -> Document:
    """The model of canonical document ``text`` after :func:`unitracker_grammar.expand.expanded`:
    the sugar of format 2.3 and the prelude's restart programs written out; CanonicalError where
    a name is defined twice."""
    doc = top_level(_read(text))
    names = [(s.kind, k) for s in doc.defs for k, _ in s.records] + [
        ("mod", k) for k, _ in doc.mods
    ]
    if twice := sorted(k for (_, k), n in Counter(names).items() if n > 1):
        raise CanonicalError(f"{', '.join(twice)} is defined twice")
    return expanded(doc)


def meta_texts(doc: Document) -> dict:
    """The document's ``meta`` texts, each as the text it spells."""
    return {k: _text(v) for k, v in doc.header.meta}


def _text(written: str) -> str:
    if written[:1] == "'":
        return written[1:-1].replace("''", "'")
    return json.loads(written) if written[:1] == '"' else written
