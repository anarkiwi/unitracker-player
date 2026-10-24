"""Rewrites a canonical document with every definition inline at its use: instruments, programs
and phrases, and template calls as anonymous code bodies and quoted expressions, the top-level
definition sections and the rows' phrase sections dropped."""

import functools

from lark import Lark, Token, Tree
from unitracker_grammar import GRAMMAR
from unitracker_grammar import code as C
from unitracker_grammar.document import printed, read
from unitracker_grammar import expr as E

DEFINED = {"INS", "CTRL", "PW", "FILTER", "RESTART", "PHRASE"}
SECTIONS = {f"def_{k}" for k in ("mod", "restart", "ins", "ctrl", "pw", "filter", "phrase")}
RUNS = {"row_run", "cmd_run"}
KIND = {"CTRL": "ctrl", "PW": "pw", "FILTER": "filter"}
CELL = {"PW": "pw", "FILTER": "cut"}


@functools.lru_cache(maxsize=1)
def parser() -> Lark:
    """The canonical parser with each subtree's span in the text."""
    return Lark(
        GRAMMAR.read_text(encoding="utf-8"),
        parser="lalr",
        lexer="contextual",
        propagate_positions=True,
    )


def _span(node) -> tuple[int, int]:
    if isinstance(node, Token):
        return node.start_pos, node.end_pos
    return node.meta.start_pos, node.meta.end_pos


class _Inliner:
    """One document's definitions, each reference's expansion and the edits that drop them."""

    def __init__(self, text: str):
        self.text, self.tree = text, parser().parse(text)
        self.defs, self.templates, self.refs, keys = {}, {}, [], set()
        for node in self.tree.iter_subtrees():
            if node.data in SECTIONS:
                for key, body in zip(node.children[::2], node.children[1::2]):
                    keys.add(key.start_pos)
                    if key.type == "SIGNATURE":
                        name, declared = C.signature(key)
                        self.templates[name] = declared, body
                    else:
                        self.defs[str(key)] = body
            for child in node.children:
                if isinstance(child, Token) and child.type in DEFINED | {"CALL"}:
                    self.refs.append((child, node.data))
        self.refs = [(t, p) for t, p in self.refs if t.start_pos not in keys]

    def expand(self, start: int, end: int, stack=()) -> str:
        """The text of ``[start, end)`` with each reference in it expanded."""
        out, at = [], start
        for token, parent in sorted(self.refs, key=lambda r: r[0].start_pos):
            if start <= token.start_pos and token.end_pos <= end:
                out += [self.text[at : token.start_pos], self.reference(token, parent, stack)]
                at = token.end_pos
        return "".join(out) + self.text[at:end]

    def body(self, name: str, stack: tuple) -> str:
        if name in stack:
            raise ValueError(f"{name} refers to itself")
        return self.expand(*_span(self.defs[name]), (*stack, name))

    def reference(self, token: Token, parent: str, stack: tuple) -> str:
        """The inline form of a reference: a run's program after its kind, a field's or an
        event's definition, a play entry's phrase, a call's body."""
        if token.type == "CALL":
            return self.call(C.call(token))
        if parent not in RUNS:
            return self.body(str(token), stack)
        node = self.defs[str(token)]
        if node.data == "modulation" and isinstance(node.children[0], Tree):
            node = node.children[0] if node.children[0].data == "program" else node
        if node.data == "program":
            return KIND[token.type] + self.expand(*_span(node), (*stack, str(token)))
        binding = self.body(str(token), stack)
        return f"{KIND[token.type]}[{{{CELL[token.type]}:{binding}}},end]"

    def call(self, value: C.Call) -> str:
        """An anonymous body with each parameter's immediate its byte, or the quoted expression
        with each parameter its integer."""
        declared, body = self.templates[value.name]
        bag = dict(zip(declared, value.args))
        if body.data == "expr":
            return f"'{E.printed(E.bind(E.Nodes().transform(body), bag))}'"
        statements = tuple(
            (
                s._replace(operand=C.Imm(_byte(bag[s.operand.value], s.operand.byte)))
                if isinstance(s, C.Op)
                and isinstance(s.operand, C.Imm)
                and isinstance(s.operand.value, str)
                else s
            )
            for s in C.Statements().transform(body)
        )
        return C.inline(statements)

    def edits(self) -> list:
        """``(start, end, text)`` of each top-level replacement: the dropped sections and each
        row's head, left with its play entries, and the references elsewhere."""
        out = [
            (*_span(s), "")
            for s in self.tree.children
            if isinstance(s, Tree) and s.data in SECTIONS
        ]
        for row in self.tree.find_data("row"):
            if row.children[0].data == "def_phrase":
                out.append((row.meta.start_pos, _span(row.children[-1])[0], " - play:"))
        return out


def _byte(value: int, byte: str) -> int:
    word = value & 0xFFFF
    return word >> 8 if byte == ">" else word & 0xFF


def inlined(text: str) -> str:
    """``text`` with every definition inline at its use (see the module), in its one canonical
    text."""
    doc = _Inliner(text)
    out, at = [], 0
    for start, end, replacement in sorted(doc.edits()):
        out += [doc.expand(at, start), replacement]
        at = end
    return printed(read("".join(out) + doc.expand(at, len(text))))
