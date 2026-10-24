"""A canonical document as plain dataclasses, transformed from its parse tree."""

import json
import re
from dataclasses import dataclass, field

import ast

from lark import Token, exceptions
from unitracker_grammar import CanonicalError, parser
from unitracker_grammar.code import Statements, check
from unitracker_grammar.expr import Nodes

NONE = None
HOLD = "hold"
_MISSING = object()
_VALUES = {
    "INT",
    "SIGNED",
    "NOTE",
    "NOTE_ATOM",
    "INS",
    "CTRL",
    "PW",
    "FILTER",
    "RESTART",
    "X",
    "PHRASE",
    "XTABLE",
    "META_KEY",
    "TEXT",
    "SQUOTED",
    "DQUOTED",
    "VERSION",
    "CTRLMASK",
    "NONE",
    "HOLD",
    "NAME",
    "HOLD_CELL",
    "HOLD_EVENT",
    "CELL",
    "PHASE",
}
_PITCH = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_NOTE = re.compile(r"([A-G])(#?)(-?\d+)")
_KINDS = {"restart", "ins", "ctrl", "pw", "filter", "x", "phrase", "mod"}
_CELL = {"pw": "pw", "filter": "cut", "cut": "cut", "freq": "freq"}


def note_number(name: str) -> int:
    """The format note of a note name: ``12*octave + pitch class`` (C2 is 24, B-1 is -1)."""
    letter, sharp, octave = _NOTE.fullmatch(name).groups()
    return 12 * int(octave) + _PITCH[letter] + (1 if sharp else 0)


@dataclass(frozen=True)
class Expr:
    """A modulator expression as its node (:mod:`unitracker_grammar.expr`); with ``bag``, its
    ``(parameter, value)`` pairs, the node names a template."""

    node: ast.expr
    bag: tuple | None = None


@dataclass(frozen=True)
class Code:
    """A code template: its statements (:mod:`unitracker_grammar.code`)."""

    statements: tuple


@dataclass(frozen=True)
class Slot:
    """A cell's modulator slot: its name, its default binding's name, and the
    ``((event, k), ...)`` it holds and resets on."""

    name: str
    mod: str | None = None
    hold: tuple = ()
    reset: tuple = ()


@dataclass(frozen=True)
class Row:
    """A program row: its timing and its other keys in vocabulary order."""

    wait: int = 0
    hold: int = 1
    keys: tuple = ()


@dataclass(frozen=True)
class Program:
    """Rows in the order they run, then ``end`` (``loop`` None) or ``{loop: k}``; ``fresh``
    for an instrument's binding fields, whose bindings restart φ."""

    rows: tuple
    loop: int | None = None
    fresh: bool = False


@dataclass(frozen=True)
class Instrument:
    """An instrument's fields; a program field holds a name or a program, ``freq`` its
    ``(key, binding)`` fields, ``key`` ``freq`` or ``slot.freq`` and a binding an Expr, an
    instance's name or None."""

    ad: int | None = None
    sr: int | None = None
    ctrl: str | Program | None = None
    pw: str | Program | None = None
    filter: str | Program | None = None
    freq: tuple = ()
    fn: object | None = None
    p0: dict | None = None
    restart: str | Program | None = None


@dataclass(frozen=True)
class Event:
    """An event atom: its note atom, instrument name, commands in vocabulary order, keep flags
    and steps."""

    note: str
    ins: str | None = None
    commands: tuple = ()
    keep_pw: bool = False
    keep_filter: bool = False
    steps: int = 1


@dataclass(frozen=True)
class Play:
    """A play entry: ``phrase`` (a name and a transpose), ``cont`` (``~``) or ``end``."""

    kind: str
    phrase: str | None = None
    transpose: int = 0


@dataclass(frozen=True)
class Header:
    """The header lines, with their defaults; ``hold`` is ``{cell: ((event, k), ...)}``,
    ``slots`` ``{cell: (Slot, ...)}``, ``order`` the phases of a tick or None, ``state``
    ``{cell: ((name, size), ...)}``."""

    version: str
    meta: dict = field(default_factory=dict)
    cycles_per_tick: int | None = None
    clock_hz: int | None = None
    voices: int = 3
    tempo: tuple = (6, 6)
    hold: dict = field(default_factory=dict)
    slots: dict = field(default_factory=dict)
    order: tuple | None = None
    state: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Document:
    """A whole document: the header, its tables and init row, every definition by name and the
    timeline's rows of play entries with its ending (``loop`` None for ``end``); ``templates``
    the ``mod`` records, each an Expr or a Code."""

    header: Header
    tables: dict
    init: tuple | None
    instruments: dict
    programs: dict
    expressions: dict
    phrases: dict
    timeline: tuple
    loop: int | None
    templates: dict = field(default_factory=dict)


def _tok(token: Token):
    if token.type in ("INT", "SIGNED"):
        return int(token)
    return NONE if token.type == "NONE" else str(token)


def _values(children) -> list:
    """The children that carry meaning: subtrees' results and value tokens."""
    return [
        _tok(c) if isinstance(c, Token) else c
        for c in children
        if not isinstance(c, Token) or c.type in _VALUES
    ]


def _words(children) -> list[str]:
    """The keyword tokens among ``children``, stripped."""
    return [c.strip() for c in children if isinstance(c, Token) and c.type not in _VALUES]


def _link(key):
    """A link of a key chain: its own key's value (one value or a tuple) before the chain's
    rest."""

    def method(_self, children):
        rest, own = {}, []
        for c in _values(children):
            if isinstance(c, dict):
                rest = c
            else:
                own.append(c)
        if not own:
            return rest
        return {key: own[0] if len(own) == 1 else tuple(own), **rest}

    return method


def _chain(prefix, keys):
    return {f"{prefix}_{k}": _link(k) for k in keys}


class ToDocument(Nodes):
    """The parse tree of ``canonical.lark`` to a Document; each method is the grammar rule of
    its name, and an expression's rules are :class:`Nodes`'."""

    # pylint: disable=too-many-public-methods,missing-function-docstring

    def start(self, children):
        head, defs, timeline = {"tables": {}, "init": None}, {k: {} for k in _KINDS}, []
        for item in _values(children):
            tag = item[0]
            if isinstance(item, str):
                head["version"] = item
            elif tag == "row":
                for kind, entries in item[1]:
                    for name, value in entries:
                        if name in defs[kind]:
                            raise CanonicalError(f"{name} is defined twice")
                        defs[kind][name] = value
                timeline.append(item[2])
            else:
                head[tag] = item[1]
        clock = head.pop("clock", (None, None))
        header = Header(
            head.pop("version"),
            head.pop("meta"),
            *clock,
            head.pop("voices", 3),
            head.pop("tempo", (6, 6)),
            head.pop("hold", {}),
            head.pop("slots", {}),
            head.pop("order", None),
            head.pop("state", {}),
        )
        _check(defs["mod"], header.state)
        programs = {**defs["restart"], **defs["ctrl"], **defs["pw"], **defs["filter"]}
        return Document(
            header,
            head["tables"],
            head["init"],
            defs["ins"],
            programs,
            defs["x"],
            defs["phrase"],
            tuple(timeline),
            head["ending"],
            defs["mod"],
        )

    @staticmethod
    def meta(children):
        return ("meta", dict(_values(children)))

    @staticmethod
    def meta_item(children):
        return tuple(_values(children))

    @staticmethod
    def text(children):
        (token,) = children
        if token.type == "SQUOTED":
            return token[1:-1].replace("''", "'")
        if token.type == "DQUOTED":
            return json.loads(token)
        return str(token)

    @staticmethod
    def clock(children):
        return ("clock", tuple(_values(children)))

    @staticmethod
    def voices(children):
        return ("voices", _values(children)[0])

    @staticmethod
    def tempo(children):
        values = _values(children)
        return ("tempo", (values[0], values[-1]))

    @staticmethod
    def hold(children):
        return ("hold", dict(_values(children)))

    @staticmethod
    def hold_cell(children):
        return tuple(_values(children))

    @staticmethod
    def events(children):
        return tuple(
            (str(name), int(k or 0)) for name, _, k in (e.partition("-") for e in children)
        )

    @staticmethod
    def order(children):
        return ("order", tuple(_values(children)))

    @staticmethod
    def state(children):
        (cells,) = children
        names = [n for variables in cells.values() for n, _ in variables]
        if len(set(names)) != len(names):
            raise CanonicalError(f"state variables declared twice: {names}")
        return ("state", cells)

    @staticmethod
    def state_vars(children):
        return tuple((str(name), int(size)) for name, size in (c.children for c in children))

    @staticmethod
    def slots(children):
        return ("slots", dict(_values(children)))

    @staticmethod
    def slot_cell(children):
        cell, *slots = _values(children)
        return cell, tuple(slots)

    @staticmethod
    def slot(children):
        name, *rest = _values(children)
        return Slot(name, **(rest[0] if rest else {}))

    @staticmethod
    def tables(children):
        values = _values(children)
        return ("tables", dict(zip(values[::2], values[1::2])))

    @staticmethod
    def intlist(children):
        return tuple(_values(children))

    @staticmethod
    def init(children):
        return ("init", tuple(_values(children)))

    @staticmethod
    def row(children):
        values = _values(children)
        return ("row", values[:-1], values[-1])

    @staticmethod
    def ending(children):
        values = _values(children)
        return ("ending", values[0] if values else None)

    @staticmethod
    def plays(children):
        return tuple(_values(children))

    @staticmethod
    def play(children):
        values = _values(children)
        if values:
            return Play("phrase", values[0], values[1] if len(values) > 1 else 0)
        return Play("cont" if _words(children) == ["~"] else "end")

    @staticmethod
    def section(children):
        kind = {"name": "mod"}.get(children[0].type.lower(), children[0].type.lower())
        entries = []
        for c in _values(children):
            if isinstance(c, str):
                entries.append([c])
            elif isinstance(c, dict):
                entries[-1][1] = Expr(entries[-1][1], tuple(c.items()))
            else:
                entries[-1].append(c)
        if kind in ("x", "mod"):
            return (kind, [(n, v if isinstance(v, (Expr, Code)) else Expr(v)) for n, v in entries])
        if kind in ("pw", "filter"):
            return (kind, [(n, _modulation(kind, [(kind, v)])) for n, v in entries])
        return (kind, [tuple(e) for e in entries])

    @staticmethod
    def instrument(children):
        values = dict(_values(children)[0])
        for kind in ("pw", "filter", "freq"):
            fields = [(k, values.pop(k)) for k in list(values) if k.rpartition(".")[2] == kind]
            if fields:
                values[kind] = tuple(fields) if kind == "freq" else _modulation(kind, fields, True)
        return Instrument(**values)

    @staticmethod
    def program(children):
        values = _values(children)
        if values and isinstance(values[-1], int):
            return Program(tuple(values[:-1]), values[-1])
        return Program(tuple(values))

    @staticmethod
    def modulation(children):
        return _values(children)[0]

    @staticmethod
    def bound(children):
        expr, bag = children
        return Expr(expr.node, tuple(bag.items()))

    @staticmethod
    def ins_fn(children):
        """``mod: NAME`` and its parameter bag as ``{fn: NAME, p0: the bag}``, the instrument's
        ``restart`` where it has no modulator, and either where the two share an instrument."""
        values = _values(children)
        name = next((v for v in values if not isinstance(v, dict)), None)
        if name is None:
            return next((c for c in values if isinstance(c, dict)), {})
        bag = next((c for c in values if isinstance(c, dict) and "restart" not in c), {})
        rest = next((c for c in values if isinstance(c, dict) and "restart" in c), {})
        return {"fn": name, "p0": bag, **rest}

    @staticmethod
    def param_bag(children):
        return dict(_values(children))

    @staticmethod
    def pname_val(children):
        name, value = _values(children)
        return str(name), value

    @staticmethod
    def pname_list(children):
        return list(_values(children))

    @staticmethod
    def cmd_set(children):
        """The event's ``!set`` writes, ``{key: value}``, into a one ``set`` command."""
        flat = [c for c in _values(children) if not isinstance(c, dict)]
        rest = next((c for c in children if isinstance(c, dict)), {})
        bag = dict(zip(flat[::2], flat[1::2]))
        return {"set": bag, **rest} if bag else rest

    @staticmethod
    def row_(children):
        keys = dict(*_values(children))
        return Row(keys.pop("wait", 0), keys.pop("hold", 1), tuple(keys.items()))

    @staticmethod
    def ctrl_value(children):
        (value,) = _values(children)
        if isinstance(value, int):
            return (value, None)
        ctrl, mask = value.split("^")
        return (int(ctrl) if ctrl else None, int(mask))

    @staticmethod
    def note_value(children):
        (value,) = _values(children)
        return ("rel", value) if isinstance(value, int) else ("abs", note_number(value))

    @staticmethod
    def cell_value(children):
        values = _values(children)
        return values[0] if values else NONE

    @staticmethod
    def route_value(children):
        values = _values(children)
        mask = values[0] if values else 0
        words = "".join(_words(children))
        if words.startswith("+"):
            return ("add", mask)
        return ("remove", mask) if "-" in words else ("mask", mask)

    @staticmethod
    def program_name(children):
        return _values(children)[0]

    @staticmethod
    def phrase(children):
        return tuple(_values(children))

    @staticmethod
    def event(children):
        values = _values(children)
        note, rest = values[0], values[1:]
        ins = rest.pop(0) if rest and isinstance(rest[0], str) else None
        commands, keep_pw, keep_filter = rest[0], rest[1], rest[2]
        steps = rest[3] if len(rest) > 3 else 1
        return Event(note, ins, tuple(commands.items()), keep_pw, keep_filter, steps)

    @staticmethod
    def cmd_filter(children):
        rest = next((c for c in children if isinstance(c, dict)), {})
        return {"filter": NONE, **rest} if _words(children) else rest

    @staticmethod
    def cmd_legato(children):
        return {"legato": True} if children else {}

    @staticmethod
    def keep_pw(children):
        return bool(children)

    @staticmethod
    def keep_filter(children):
        return bool(children)

    def fexpr(self, children):
        return Expr(super().fexpr(children))

    def code(self, children):
        return Code(Statements.code(self, children))

    code_label, code_op = Statements.code_label, Statements.code_op
    code_imm, code_mem = Statements.code_imm, Statements.code_mem


def _check(templates: dict, state: dict):
    """Checks the code ``templates``: none's label names a state variable of ``state``."""
    names = {n for variables in state.values() for n, _ in variables}
    for template in templates.values():
        if isinstance(template, Code):
            check(template.statements, names)


def _slot_link(key):
    """A slot's key chain link: ``mod`` its name, ``hold`` and ``reset`` their events."""

    def method(_self, children):
        own = [c for c in children if not isinstance(c, dict)]
        rest = next((c for c in children if isinstance(c, dict)), {})
        return {key: str(own[0]) if key == "mod" else own[0], **rest} if own else rest

    return method


def _slotted(cell: str):
    """A modulated cell's link of a key chain: its own key (``cell``, or ``slot.cell`` under a
    slot prefix or a slotted command) and value before the chain's rest."""

    def method(_self, children):
        rest, own, key = {}, [], cell
        for c in _values(children):
            if isinstance(c, dict):
                rest = c
            else:
                own.append(c)
        for c in children:
            if isinstance(c, Token) and c.type == "SLOT":
                key = c + cell
            elif isinstance(c, Token) and c.type.startswith("CMD_"):
                key = c[1:-1]
        return {key: own[0], **rest} if own else rest

    return method


for _name, _method in {
    **_chain("row", "wait hold ctrl gate note ad sr res route mode run filter vol".split()),
    **_chain("ins", "ad sr ctrl restart".split()),
    **_chain("cmd", "run ad sr ctrl resfilt mode vol tempo vtempo".split()),
    **{f"{chain}_{c}": _slotted(c) for chain in ("row", "cmd") for c in ("pw", "cut", "freq")},
    **{f"ins_{c}": _slotted(c) for c in ("pw", "filter", "freq")},
    **{f"slot_{k}": _slot_link(k) for k in ("mod", "hold", "reset")},
    **{f"state_{c}": _link(c) for c in ("freq", "pw", "cut")},
}.items():
    setattr(ToDocument, _name, _method)


def _program(value) -> bool:
    """Whether a pulse or filter field is a program or a program's name, not a binding."""
    return isinstance(value, Program) or isinstance(value, str) and not value.startswith("x")


def _modulation(kind: str, fields: list, fresh: bool = False):
    """A pulse or filter modulation's ``(key, value)`` fields as one program: a program or its
    name, unprefixed and alone, is itself; bindings are one row binding each one's slot of the
    cell."""
    if len(fields) == 1 and fields[0][0] == kind and _program(fields[0][1]):
        return fields[0][1]
    if any(_program(value) for _, value in fields):
        raise CanonicalError(f"{kind}: a program is the one {kind} field and takes no slot")
    cell = _CELL[kind]
    keys = tuple((k[: -len(kind)] + cell, v) for k, v in fields)
    return Program((Row(keys=keys),), fresh=fresh)


def read(text: str) -> Document:
    """The Document of canonical document ``text``."""
    try:
        tree = parser().parse(text)
    except exceptions.UnexpectedInput as exc:
        raise CanonicalError(
            f"line {exc.line} column {exc.column}: {str(exc).splitlines()[0]}"
        ) from exc
    try:
        return ToDocument().transform(tree)
    except exceptions.VisitError as exc:
        raise exc.orig_exc from exc
