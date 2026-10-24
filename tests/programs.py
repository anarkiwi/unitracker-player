"""Rewrites format 1.23's program templates as text: ``expanded`` names each call's program, one
named record per distinct call with each parameter its argument, the templates dropped;
``templated`` makes each named program a template over its literal integers, each reference a
call passing them."""

import ast
import itertools
import re

from inline import parser
from lark import Token, Tree
from unitracker_grammar import code as C
from unitracker_grammar.document import printed, read
from unitracker_grammar import expr as E

NAMED = {"RESTART": "restart", "CTRL": "ctrl", "PW": "pw", "FILTER": "filter"}
SIGNED = {f"{k}_SIG": v for k, v in NAMED.items()}
PREFIX = {"restart": "r", "ctrl": "c", "pw": "pw", "filter": "f"}
INTEGER = {f"row_{k}" for k in "wait hold gate ad sr res mode vol".split()}
CELLS = ("cell_value", "modulation")


def _records(tree: Tree, types: dict):
    """``(key, body, section)`` of each program record keyed by a token of ``types``."""
    for node in tree.find_data("section"):
        for key, body in zip(node.children, node.children[1:]):
            if isinstance(key, Token) and key.type in types:
                yield key, body, node


def _line(key: Token, body: Tree) -> tuple[int, int]:
    """The span of a record's line, its indentation and its newline included."""
    return key.start_pos - key.column + 1, body.meta.end_pos + 1


def _bare(node: Tree):
    """A cell value's expression, when it is one."""
    return E.Nodes().transform(node) if node.data == "fexpr" else None


def _sites(node: Tree, out: list, inline: bool = False) -> list:
    """``(start, end, what, value)`` of each place ``node`` reads a parameter or holds a
    literal a template may take as one: an integer row key's ``int``, a relative ``note``, a
    cell's ``name`` or ``const``, a ``call``; nothing inside an inline program."""
    for child in node.children:
        if isinstance(child, Token):
            what = None
            if child.type == "PARAM" and node.data != "factor":
                what = "name"
            elif child.type == "REL_PARAM" or child.type == "SIGNED" and node.data == "note_value":
                what = "note"
            elif child.type == "INT" and (
                node.data in INTEGER | {"ctrl_value"}
                or node.data == "route_value"
                and len(node.children) == 1
            ):
                what = "int"
            elif child.type == "CALL":
                what = "call"
            if what and not inline:
                out.append((child.start_pos, child.end_pos, what, str(child)))
        elif node.data in CELLS and (value := _bare(child)) is not None:
            if isinstance(value, ast.Name) and E.params(value) and not inline:
                out.append((child.meta.start_pos, child.meta.end_pos, "name", value.id))
            elif isinstance(value, ast.Constant) and not inline:
                out.append((child.meta.start_pos, child.meta.end_pos, "const", value.value))
        else:
            _sites(child, out, inline or child.data == "run_program")
    return out


class _Expander:
    """One document's program templates and the named program of each of their calls."""

    def __init__(self, text: str):
        self.text, self.tree = text, parser().parse(text)
        self.forms, self.names, self.lines = {}, {}, {}
        records = _records(self.tree, NAMED | SIGNED)
        aliases = {b.meta.start_pos for _, b, _ in records if isinstance(b.children[0], Token)}
        self.sites = sorted(
            (b, e, "alias" if what == "call" and b in aliases else what, value)
            for b, e, what, value in _sites(self.tree, [])
        )
        used = [int(m) for m in re.findall(r"\b(?:pw|[cfr])(\d+)\b", text)]
        self.number = itertools.count(max(used, default=0) + 1)
        for key, body, _ in _records(self.tree, SIGNED):
            name, declared = C.signature(key)
            self.forms[name] = SIGNED[key.type], declared, body, _line(key, body)
            self.indent = " " * (key.column - 1)

    def render(self, start: int, end: int, bag: dict) -> str:
        """``[start, end)`` with each parameter its value in ``bag``, each call of a program
        template its program's name and each template's line a mark of its name."""
        out, at = [], start
        marks = [(*span, "line", name) for name, (*_, span) in self.forms.items()]
        for begin, stop, what, value in sorted(self.sites + marks):
            if start <= begin < end and begin >= at:
                out.append(self.text[at:begin])
                out.append(f"\0{value}\0" if what == "line" else self.site(what, value, bag))
                at = stop
        out.append(self.text[at:end])
        return "".join(out)

    def site(self, what: str, value, bag: dict) -> str:
        """A site's text: a parameter's value, a call's arguments bound, its program's name
        or, where a record is the call, its program."""
        if what == "name":
            return str(bag[value]) if value in bag else value
        if what == "note" and value[1:] in bag:
            return f"{bag[value[1:]] * (-1 if value[0] == '-' else 1):+d}"
        if what in ("call", "alias"):
            call = C.call(value)
            call = C.Call(call.name, tuple(bag.get(a, a) for a in call.args))
            if call.name not in self.forms:
                return str(call)
            return self.body(call) if what == "alias" else self.named(call)
        return str(value)

    def body(self, call: C.Call) -> str:
        """The text of a call's program."""
        _, declared, body, _ = self.forms[call.name]
        return self.render(body.meta.start_pos, body.meta.end_pos, dict(zip(declared, call.args)))

    def named(self, call: C.Call) -> str:
        """The name of a call's program, its record made at its first use."""
        if call not in self.names:
            name = f"{PREFIX[self.forms[call.name][0]]}{next(self.number)}"
            self.names[call] = name
            line = f"{self.indent}{name}: {self.body(call)}\n"
            self.lines.setdefault(call.name, []).append(line)
        return self.names[call]

    def document(self) -> str:
        text = self.render(0, len(self.text), {})
        for name in self.forms:
            text = text.replace(f"\0{name}\0", "".join(self.lines.get(name, [])))
        text = re.sub(r"(run: |!run:)(?:ctrl|pw|filter):", r"\1", text)
        return re.sub(r"(?m)^( *)(?:restart|ctrl|pw|filter):\n(?!\1  )", "", text)


def expanded(text: str) -> str:
    """``text`` with each call of a program template a named program's name (see the module)."""
    return printed(read(_Expander(text).document()))


TOP = ("restart", "ins", "ctrl", "pw", "filter")
FIELDS = {"ins_ctrl": "ctrl", "ins_restart": "restart", "ins_pw": "pw", "ins_filter": "filter"}


def _lifted(tree: Tree):
    """``(kind, program)`` of each instrument's program field, the one pulse or filter field
    where it is a program."""
    for node in tree.iter_subtrees_topdown():
        programs = [c for c in node.children if isinstance(c, Tree)][:1]
        if node.data not in FIELDS or not programs:
            continue
        body = programs[0].children[0] if programs[0].data == "modulation" else programs[0]
        if isinstance(body, Tree) and body.data == "program":
            yield FIELDS[node.data], body


def _placed(text: str, tree: Tree, lines: dict) -> list:
    """The edits adding each kind's ``lines`` to its top-level section, or a new one where the
    kinds' order puts it."""
    top = {
        (NAMED | SIGNED | {"INS": "ins"})[s.children[0].type]: s
        for s in tree.children
        if isinstance(s, Tree) and s.data == "section"
    }
    end = re.search(r"(?m)^(?:init: |song:\n)", text).start()
    edits = []
    for k, kind in enumerate(TOP):
        if kind in top and kind in lines:
            at = top[kind].children[-1].meta.end_pos + 1
            edits.append((at, at, "".join(lines[kind])))
        elif kind in lines:
            at = min([top[later].meta.start_pos for later in TOP[k:] if later in top] + [end])
            edits.append((at, at, f"{kind}:\n" + "".join(lines[kind])))
    return edits


def _template(body: Tree, edits: list) -> tuple[str, str]:
    """``edits`` extended by a program's literals made its parameters, ``q0``, ``q1``, ...;
    ``(declared, arguments)``: its parameters and its call's arguments, each spelled."""
    values = []
    for start, end, what, value in _sites(body, []):
        if what == "call":
            call = C.call(value)
            spelled = []
            for arg in call.args:
                spelled.append(arg if isinstance(arg, str) else f"q{len(values)}")
                values += [] if isinstance(arg, str) else [arg]
            edits.append((start, end, f"{call.name}({','.join(spelled)})"))
        elif what != "name":
            edits.append((start, end, f"{'+' * (what == 'note')}q{len(values)}"))
            values.append(int(value))
    return ", ".join(f"q{k}" for k in range(len(values))), ",".join(map(str, values))


def _edited(text: str, start: int, end: int, edits) -> str:
    """``[start, end)`` of ``text`` with each ``(start, end, replacement)`` of ``edits`` in it
    made."""
    out, at = [], start
    for begin, stop, replacement in sorted(edits, key=lambda e: e[:2]):
        out += [text[at:begin], replacement]
        at = stop
    return "".join(out) + text[at:end]


def _references(tree: Tree, calls: dict) -> list:
    """The edits making each reference to a named program its template's call, half the runs
    with the call after its kind."""
    refs = []
    for node in tree.iter_subtrees():
        for child in node.children:
            if isinstance(child, Token) and child.type in NAMED and node.data != "section":
                prefixed = node.data == "program_name" and len(refs) % 2
                kind = f"{NAMED[child.type]}:" if prefixed else ""
                refs.append((child.start_pos, child.end_pos, kind + calls[str(child)]))
    return refs


def templated(text: str) -> str:
    """``text`` with each named program, and each instrument's program field in a row, a
    template over its literals (see the module)."""
    tree = parser().parse(text)
    edits, calls = [], {}
    for key, body, _ in _records(tree, NAMED):
        declared, args = _template(body, edits)
        edits.append((key.start_pos, key.end_pos, f"t_{key}({declared})"))
        calls[str(key)] = f"t_{key}({args})"
    refs = _references(tree, calls)
    lines = {}
    for k, (kind, body) in enumerate(_lifted(tree)):
        span = body.meta.start_pos, body.meta.end_pos
        inner = [r for r in refs if span[0] <= r[0] < span[1]]
        refs = [r for r in refs if r not in inner]
        declared, args = _template(body, inner)
        edits.append((*span, f"t{k}({args})"))
        lines.setdefault(kind, []).append(f"  t{k}({declared}): {_edited(text, *span, inner)}\n")
    return printed(read(_edited(text, 0, len(text), edits + refs + _placed(text, tree, lines))))
