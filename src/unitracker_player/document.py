"""A canonical document as plain dataclasses, transformed from its parse tree."""

import json
import re
from dataclasses import dataclass, field

import ast

from lark import Token, exceptions
from unitracker_grammar import CanonicalError, parser
from unitracker_grammar.code import (
    Call,
    Statements,
    call,
    check,
    declare,
    params,
    programs,
    signature,
)
from unitracker_grammar.expr import Nodes
from unitracker_grammar.expr import params as expr_params

NONE = None
HOLD = "hold"
RESET = "reset"
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
    "PHRASE",
    "XTABLE",
    "META_KEY",
    "TEXT",
    "SQUOTED",
    "DQUOTED",
    "CTRLMASK",
    "NONE",
    "HOLD",
    "RESET",
    "NAME",
    "HOLD_EVENT",
    "CELL",
    "PHASE",
    "SIGNATURE",
    "CALL",
    "PROGRAM_KIND",
    "PARAM",
    "REL_PARAM",
    "PROGRAM_SIG",
}
_PITCH = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_NOTE = re.compile(r"([A-G])(#?)(-?\d+)")
KINDS = ("mod", "restart", "ins", "ctrl", "pw", "filter", "phrase")
_CELL = {"pw": "pw", "filter": "cut", "cut": "cut", "freq": "freq"}


def note_number(name: str) -> int:
    """The format note of a note name: ``12*octave + pitch class`` (C2 is 24, B-1 is -1)."""
    letter, sharp, octave = _NOTE.fullmatch(name).groups()
    return 12 * int(octave) + _PITCH[letter] + (1 if sharp else 0)


@dataclass(frozen=True)
class Expr:
    """A modulator expression as its node (:mod:`unitracker_grammar.expr`)."""

    node: ast.expr


@dataclass(frozen=True)
class Param:
    """A program template's parameter where a row reads it: its value times ``sign``, -1 for a
    relative note's ``-p``."""

    name: str
    sign: int = 1


@dataclass(frozen=True)
class Code:
    """A code template, or as a binding an anonymous code body: its statements
    (:mod:`unitracker_grammar.code`)."""

    statements: tuple


@dataclass(frozen=True)
class Slot:
    """A cell's modulator slot: its name, its default binding (a name or a Call), and the
    ``((event, k), ...)`` it holds and resets on."""

    name: str
    mod: object = None
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
    """An instrument's fields; a program field holds a name, a call or a program, ``freq`` its
    ``(key, binding)`` fields, ``key`` ``freq`` or ``slot.freq`` and a binding a modulator or
    None."""

    ad: int | None = None
    sr: int | None = None
    ctrl: str | Program | None = None
    pw: str | Program | None = None
    filter: str | Program | None = None
    freq: tuple = ()
    restart: str | Program | None = None


@dataclass(frozen=True)
class Event:
    """An event atom: its note atom, instrument (a name or an inline Instrument), commands in
    vocabulary order, keep flags and steps."""

    note: str
    ins: str | None = None
    commands: tuple = ()
    keep_pw: bool = False
    keep_filter: bool = False
    steps: int = 1


@dataclass(frozen=True)
class Play:
    """A play entry: ``phrase`` (a name or an inline phrase's events, and a transpose), ``cont``
    (``~``) or ``end``."""

    kind: str
    phrase: str | tuple | None = None
    transpose: int = 0


@dataclass(frozen=True)
class Header:
    """The header lines, with their defaults; ``slots`` ``{cell: (Slot, ...)}``, ``order`` the
    phases of a tick or None, ``state`` ``{cell: ((name, size), ...)}``, ``regs`` the registers
    before tick 0 or None."""

    meta: dict = field(default_factory=dict)
    cycles_per_tick: int | None = None
    clock_hz: int | None = None
    voices: int = 3
    tempo: tuple = (6, 6)
    slots: dict = field(default_factory=dict)
    order: tuple | None = None
    state: dict = field(default_factory=dict)
    regs: tuple | None = None


@dataclass(frozen=True)
class Document:
    """A whole document: the header, its tables and init row, every definition by name and the
    timeline's rows of play entries with its ending (``loop`` None for ``end``); ``templates``
    the modulator templates by name, each an Expr or a Code, ``signatures`` their parameters,
    ``{name: parameters}``, and ``forms`` the program templates, ``{name: (kind, parameters,
    program)}``."""

    header: Header
    tables: dict
    init: tuple | None
    instruments: dict
    programs: dict
    forms: dict
    phrases: dict
    timeline: tuple
    loop: int | None
    templates: dict
    signatures: dict


def _tok(token: Token):
    if token.type in ("INT", "SIGNED"):
        return int(token)
    if token.type == "CALL":
        return call(token)
    if token.type in ("PARAM", "REL_PARAM"):
        return Param(token.lstrip("+-"), -1 if token[0] == "-" else 1)
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


class _Chain(list):
    """A key chain's ``(key, value)`` pairs from a link on, in the order written."""


def _split(children) -> tuple:
    """A link's own values and the chain's rest after it."""
    own, rest = [], _Chain()
    for c in _values(children):
        if isinstance(c, _Chain):
            rest = c
        else:
            own.append(c)
    return own, rest


def _link(key):
    """A link of a key chain: its own key's value (one value or a tuple) before the chain's
    rest."""

    def method(_self, children):
        own, rest = _split(children)
        if not own:
            return rest
        return _Chain([(key, own[0] if len(own) == 1 else tuple(own))] + rest)

    return method


def _paired(children) -> list:
    """A definition section's ``(name, value)`` records."""
    values = _values(children)
    return list(zip(map(str, values[::2]), values[1::2]))


def _section(kind: str):
    """A definition section's rule: ``(kind, records)``."""
    return lambda _self, children: (kind, _paired(children))


def _chain(prefix, keys):
    return {f"{prefix}_{k}": _link(k) for k in keys}


class ToDocument(Nodes):
    """The parse tree of ``canonical.lark`` to a Document; each method is the grammar rule of
    its name, and an expression's rules are :class:`Nodes`'."""

    # pylint: disable=too-many-public-methods,missing-function-docstring

    def start(self, children):
        head, defs, timeline = {"tables": {}, "init": None}, {k: {} for k in KINDS}, []
        for item in _values(children):
            tag = item[0]
            if tag in KINDS or tag == "row":
                for kind, entries in [item] if tag in KINDS else item[1]:
                    for name, value in entries:
                        if name in defs[kind]:
                            raise CanonicalError(f"{name} is defined twice")
                        defs[kind][name] = value
                if tag == "row":
                    timeline.append(item[2])
            else:
                head[tag] = item[1]
        header = Header(
            head.pop("meta"),
            *head.pop("clock", (None, None)),
            head.pop("voices", 3),
            head.pop("tempo", (6, 6)),
            head.pop("slots", {}),
            head.pop("order", None),
            head.pop("state", {}),
            head.pop("regs", None),
        )
        return Document(
            header,
            head["tables"],
            head["init"],
            defs["ins"],
            *_programs(defs),
            defs["phrase"],
            tuple(timeline),
            head["ending"],
            *_check(defs["mod"], header.state),
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
    def events(children):
        return tuple(
            (str(name), int(k or 0)) for name, _, k in (e.partition("-") for e in children)
        )

    @staticmethod
    def order(children):
        return ("order", tuple(_values(children)))

    @staticmethod
    def state(children):
        cells = dict(children[0])
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
    def regs(children):
        return ("regs", children[0])

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
    def def_mod(children):
        return ("mod", [(k, v if isinstance(v, Code) else Expr(v)) for k, v in _paired(children)])

    @staticmethod
    def def_pw(children):
        return ("pw", [(n, _modulation("pw", [("pw", v)])) for n, v in _paired(children)])

    @staticmethod
    def def_filter(children):
        return (
            "filter",
            [(n, _modulation("filter", [("filter", v)])) for n, v in _paired(children)],
        )

    @staticmethod
    def instrument(children):
        fields, values = _values(children)[0], {}
        for key, value in fields:
            if key.rpartition(".")[2] not in ("pw", "filter", "freq"):
                values[key] = value
        for kind in ("pw", "filter", "freq"):
            own = [(k, v) for k, v in fields if k.rpartition(".")[2] == kind]
            if own:
                values[kind] = tuple(own) if kind == "freq" else _modulation(kind, own, True)
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
    def row_(children):
        keys = _values(children)[0] if children else ()
        timing = dict(k for k in keys if k[0] in ("wait", "hold"))
        rest = tuple(k for k in keys if k[0] not in ("wait", "hold"))
        return Row(timing.get("wait", 0), timing.get("hold", 1), rest)

    @staticmethod
    def ctrl_value(children):
        (value,) = _values(children)
        if not isinstance(value, str):
            return (value, None)
        ctrl, mask = value.split("^")
        return (int(ctrl) if ctrl else None, int(mask))

    @staticmethod
    def note_value(children):
        (value,) = _values(children)
        return ("abs", note_number(value)) if isinstance(value, str) else ("rel", value)

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
    def run_program(children):
        """An inline program after its kind, ``(kind, Program)``."""
        return tuple(_values(children))

    @staticmethod
    def phrase(children):
        return tuple(_values(children))

    @staticmethod
    def event(children):
        values = _values(children)
        note, rest = values[0], values[1:]
        ins = rest.pop(0) if rest and isinstance(rest[0], (str, Instrument)) else None
        commands, keep_pw, keep_filter = rest[0], rest[1], rest[2]
        steps = rest[3] if len(rest) > 3 else 1
        return Event(note, ins, tuple(commands), keep_pw, keep_filter, steps)

    @staticmethod
    def cmd_legato(children):
        return _Chain([("legato", True)] if children else [])

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

    asm = code
    code_label, code_op = Statements.code_label, Statements.code_op
    code_imm, code_mem = Statements.code_imm, Statements.code_mem


def _check(records: dict, state: dict) -> tuple[dict, dict]:
    """The templates of the ``mod`` records by name and their ``{name: parameters}``, checked:
    no code template's label names a state variable of ``state``, and a signature declares
    what its body reads, no state variable among them."""
    names = {n for variables in state.values() for n, _ in variables}
    templates, signatures = {}, {}
    for key, template in records.items():
        if isinstance(template, Code):
            check(template.statements, names)
            used = params(template.statements)
        else:
            used = expr_params(template.node)
        key, declared = declare(key, used, names)
        signatures[key] = declared
        if key in templates:
            raise CanonicalError(f"{key} is defined twice")
        templates[key] = template
    return templates, signatures


def _programs(defs: dict) -> tuple[dict, dict]:
    """The named programs of the definitions by name, and the program templates as
    ``{name: (kind, parameters, program)}``."""
    named, forms = {}, {}
    for kind in ("restart", "ctrl", "pw", "filter"):
        for key, value in defs[kind].items():
            if "(" not in key:
                named[key] = value
                continue
            name, declared = signature(key)
            if name in forms:
                raise CanonicalError(f"program template {name} is defined twice")
            forms[name] = (kind, declared, value)
    return named, forms


def _slot_link(key):
    """A slot's key chain link: ``mod`` its name, ``hold`` and ``reset`` their events."""

    def method(_self, children):
        own = [c for c in children if not isinstance(c, dict)]
        rest = next((c for c in children if isinstance(c, dict)), {})
        if own and key == "mod" and isinstance(own[0], Token):
            own[0] = call(own[0]) if own[0].type == "CALL" else str(own[0])
        return {key: own[0], **rest} if own else rest

    return method


def _slotted(cell: str):
    """A modulated cell's link of a key chain: its own key (``cell``, or ``slot.cell`` under a
    slot prefix or a slotted command) and value before the chain's rest."""

    def method(_self, children):
        (own, rest), key = _split(children), cell
        for c in children:
            if isinstance(c, Token) and c.type == "SLOT":
                key = c + cell
            elif isinstance(c, Token) and c.type.startswith("CMD_"):
                key = c[1:-1]
        return _Chain([(key, own[0])] + rest) if own else rest

    return method


for _name, _method in {
    **_chain("row", "wait hold ctrl gate note ad sr res route mode run filter vol".split()),
    **_chain("ins", "ad sr ctrl restart".split()),
    **_chain("cmd", "run ad sr ctrl resfilt mode vol tempo vtempo".split()),
    **{f"{chain}_{c}": _slotted(c) for chain in ("row", "cmd") for c in ("pw", "cut", "freq")},
    **{f"ins_{c}": _slotted(c) for c in ("pw", "filter", "freq")},
    **{f"slot_{k}": _slot_link(k) for k in ("mod", "hold", "reset")},
    **{f"state_{c}": _link(c) for c in ("freq", "pw", "cut")},
    **{f"def_{k}": _section(k) for k in ("restart", "ins", "ctrl", "phrase")},
}.items():
    setattr(ToDocument, _name, _method)


def _program(value) -> bool:
    """Whether a pulse or filter field is a program or a program's name, not a binding."""
    return isinstance(value, (Program, str))


def _modulation(kind: str, fields: list, fresh: bool = False):
    """A pulse or filter modulation's ``(key, value)`` fields as one program: a program or its
    name, unprefixed and alone, is itself, as is a call there, which the compiler resolves to a
    program template's or a binding of a modulator template's; bindings are one row binding
    each one's slot of the cell."""
    if len(fields) == 1 and fields[0][0] == kind:
        if isinstance(fields[0][1], Call) or _program(fields[0][1]):
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
    programs(tree)
    try:
        return ToDocument().transform(tree)
    except exceptions.VisitError as exc:
        raise exc.orig_exc from exc
