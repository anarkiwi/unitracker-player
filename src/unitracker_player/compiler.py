"""A Document compiled for playing: its names resolved and its programs, instruments and
modulators compiled once."""

import ast
from dataclasses import dataclass

from unitracker_grammar import PAL_HZ
from unitracker_grammar.code import Call, check, inline
from unitracker_grammar.code import params as code_params
from unitracker_grammar.expr import ExpressionError, bind

from unitracker_player.code import Machine, Routine
from unitracker_player.document import (
    HOLD,
    Code,
    Document,
    Expr,
    Param,
    Program,
    Row,
    TOP,
    Slot,
    note_number,
    read,
)
from unitracker_player.expr import compile_expr, constant, note_table, offset
from unitracker_player.modulation import SHAPES
from unitracker_player.sequencer import PlayError

VERSION = "1.23"
SLOTTED = "1.22"
OWNED = ("1.20", "1.21")
LEGACY = "1.20"
MAX_VOICES = 3
REGISTERS = 25
ENVELOPE = ("ad", "sr", "ctrl")
CHIP = ("cut", "resfilt", "mode", "vol")
CELLS = ("pw", "cut", "freq")
CONTROL, PULSE, FILTER = range(3)
ORDER = ("events", "ctrl", "pulse", "filter", "freq", "pw", "cut")
PHASES = (*ORDER, "read")
SLOT_PHASES = {"ctrl": CONTROL, "pulse": PULSE, "filter": FILTER}
FREQ_MASK, PW_MASK, CUT_MASK = 0xFFFF, 0xFFF, 0x7FF
KIND_CELL = {"pw": "pw", "filter": "cut"}
ROW_KEYS = frozenset("ctrl gate note ad sr pw cut res route mode freq run vol".split())


@dataclass(eq=False)
class CProgram:
    """A compiled program: rows of ``(wait, hold, ops)`` and the loop row, or None for end;
    filled after it is memoised, so a program may run itself."""

    rows: tuple = ()
    loop: int | None = None
    fresh: bool = False


@dataclass(frozen=True)
class CInstrument:
    """A compiled instrument; ``freq`` its ``(slot, fn)`` frequency modulators or None, ``fn``
    its shape's function ``f(bag, v, t, note)``, ``p0`` the bag it starts from, and ``restart``
    its rows with the ticks each precedes the note."""

    ad: int | None
    sr: int | None
    ctrl: CProgram | None
    pw: CProgram | None
    filter: CProgram | None
    freq: object
    fn: object | None
    p0: dict | None
    restart: tuple | None


@dataclass(frozen=True)
class CEvent:
    """A compiled event: note (a format note, ``off``, ``on`` or None), instrument, command ops,
    keep flags, steps, legato and tempo changes."""

    note: int | str | None
    ins: CInstrument | None
    ops: tuple
    keep_pw: bool
    keep_filter: bool
    steps: int
    legato: bool
    tempo: tuple | None
    vtempo: tuple | None


@dataclass(frozen=True)
class Song:
    """A document compiled for playing; ``slots`` is ``{cell: slot names}``, None in the formats
    whose command modulators own their cells; ``marks`` ``{mark: events}`` for each ``hold key``
    and ``reset key``, ``rows`` their row events; ``defaults`` ``{cell: ((slot, fn), ...)}``;
    ``order`` the phases of a tick; ``state`` ``{cell: bytes of its state}`` and ``code``
    whether a code modulator plays."""

    voices: int
    tempo: tuple
    clock_hz: int
    init: tuple
    timeline: tuple
    loop: int | None
    lookahead: int
    table: object
    marks: dict
    rows: dict
    horizon: int
    slots: dict | None
    defaults: dict
    order: tuple
    state: dict
    code: bool


def _tempo(value, where: str) -> tuple:
    pair = value if isinstance(value, tuple) else (value, value)
    if min(pair) < 1:
        raise PlayError(f"{where}: a tempo of 0 ticks per step")
    return pair


def _slot(name) -> int:
    """The program slot of a program's name or an inline program's kind."""
    return PULSE if name.startswith("pw") else CONTROL if name.startswith("c") else FILTER


def _kind(name: str) -> str:
    """The kind of a program's name."""
    return {"r": "restart", "c": "ctrl", "p": "pw", "f": "filter"}[name[0]]


def _bound(value, bag: dict, where):
    """A program row's value with each parameter of a template's that it reads, a Param, a
    cell value that is its name alone or a call's named argument, its integer in ``bag``."""
    if isinstance(value, Param):
        return _argument(value.name, bag, where) * value.sign
    if isinstance(value, Expr) and isinstance(value.node, ast.Name) and value.node.id in bag:
        return bag[value.node.id]
    if isinstance(value, Call):
        args = tuple(a if isinstance(a, int) else _argument(a, bag, where) for a in value.args)
        return Call(value.name, args)
    if isinstance(value, tuple):
        return tuple(_bound(v, bag, where) for v in value)
    return value


def _argument(name: str, bag: dict, where) -> int:
    if name not in bag:
        raise PlayError(f"program {where}: parameter {name} is no template's of format {VERSION}")
    return bag[name]


def _ticks(value: int, where) -> int:
    if value < 0:
        raise PlayError(f"program {where}: {value} ticks")
    return value


def _spelled(key: str, value, legacy: bool) -> tuple:
    """A row key or command in the current format's spelling: format 1.20's ``pw: none`` is
    ``pw: hold`` and its ``filter: none`` ``cut: hold``, which 1.21 alone spells."""
    if key == "filter":
        if not legacy:
            raise PlayError(f"filter: none is {LEGACY}'s spelling of cut: hold")
        return "cut", HOLD
    return key, HOLD if legacy and key == "pw" and value is None else value


class _Compiler:
    """Resolves a Document's names and compiles its programs, instruments and expressions, each
    once; each method compiles the construct of its name."""

    # pylint: disable=missing-function-docstring

    def __init__(self, doc: Document, table):
        self.doc, self.table = doc, table
        head = doc.header
        self.legacy = head.version == LEGACY
        self.memo: dict = {}
        self.slots = self.slotted = self.machine = None
        coded = any(isinstance(t, Code) for t in doc.templates.values())
        self.calls = head.version == VERSION
        if not self.calls and any((head.state, coded, doc.signatures, doc.top, doc.forms)):
            raise PlayError(f"state, code, signatures and the mod section: format {VERSION}'s")
        if self.calls and (doc.expressions or doc.top != doc.templates.keys()):
            raise PlayError(f"x, a row's mod: format {VERSION} calls its mod section's templates")
        if self.calls and doc.rows & set(TOP):
            raise PlayError(
                f"a row's {', '.join(sorted(doc.rows & set(TOP)))}: format {VERSION} "
                "defines restart, ins, ctrl, pw and filter at the top level"
            )
        if not self.calls and doc.at_top:
            raise PlayError(f"top-level {', '.join(sorted(doc.at_top))}: format {VERSION}'s")
        if head.version in OWNED:
            if head.slots:
                raise PlayError(f"slots: format {SLOTTED}'s")
        else:
            if head.hold:
                raise PlayError(f"hold: format {SLOTTED} holds in its slots line")
            self.slotted = {c: head.slots.get(c, (Slot(c),)) for c in CELLS}
            self.slots = {c: tuple(s.name for s in slots) for c, slots in self.slotted.items()}
            for cell, names in self.slots.items():
                if len(set(names)) != len(names):
                    raise PlayError(f"slots: {cell} names a slot twice")
        self.order = ORDER if head.order is None else head.order
        if head.order is not None and (self.slots is None or sorted(head.order) != sorted(PHASES)):
            raise PlayError(f"order: format {SLOTTED}'s, each of {', '.join(PHASES)} once")

    def fn(self, expr: Expr, freq: bool = False, key=None):
        """The compiled expression, memoised by its node or by ``key``."""
        node = expr.node
        if freq and constant(node) is not None:
            node = offset(constant(node))
        key = ("fn", ast.dump(node)) if key is None else key
        if key not in self.memo:
            try:
                self.memo[key] = compile_expr(node, self.table, self.doc.tables)
            except ValueError as exc:
                raise PlayError(str(exc)) from exc
        return self.memo[key]

    def named(self, kind: str, names: dict, name: str):
        if name not in names:
            raise PlayError(f"unknown {kind} {name}")
        return names[name]

    def program(self, ref, kind: str | None = None, fresh: bool = False) -> CProgram | None:
        """The compiled program of a name, an inline program, or a call of a program template
        or, in a ``kind`` pw or filter field, of a modulator template (a program binding its
        cell, ``fresh`` in an instrument)."""
        if ref is None:
            return None
        if isinstance(ref, Call):
            return self.form(ref, kind, fresh)
        key = ("program", ref if isinstance(ref, str) else id(ref))
        if key not in self.memo:
            source = self.named("program", self.doc.programs, ref) if isinstance(ref, str) else ref
            if isinstance(source, Call):
                self.memo[key] = self.program(source, _kind(ref))
                return self.memo[key]
            out = self.memo[key] = CProgram()
            self._fill(out, source, ref)
        return self.memo[key]

    def form(self, ref: Call, kind: str | None, fresh: bool) -> CProgram:
        """The program of a call: its template's with each parameter its argument, memoised by
        the call (a call of a modulator template where a pw or filter field binds one)."""
        if ref.name not in self.doc.forms:
            if kind not in KIND_CELL:
                raise PlayError(f"{ref}: {ref.name} is no program template")
            source = Program((Row(keys=((KIND_CELL[kind], ref),)),), fresh=fresh)
            return self._program(("program", ref, kind, fresh), source, ref)
        own, declared, body = self.doc.forms[ref.name]
        if kind not in (None, own):
            raise PlayError(f"{ref}: {ref.name} is a {own} program, not a {kind} program")
        if len(declared) != len(ref.args) or not all(isinstance(a, int) for a in ref.args):
            raise PlayError(f"{ref}: {ref.name} takes {len(declared)} integer argument(s)")
        key = ("program", ref)
        if isinstance(body, Call):
            if key not in self.memo:
                self.memo[key] = None
                self.memo[key] = self.program(_bound(body, dict(zip(declared, ref.args)), ref), own)
            if self.memo[key] is None:
                raise PlayError(f"{ref}: a program template calls itself")
            return self.memo[key]
        return self._program(key, body, ref, dict(zip(declared, ref.args)))

    def _program(self, key, source: Program, ref, bag=None) -> CProgram:
        if key not in self.memo:
            self._fill(self.memo.setdefault(key, CProgram()), source, ref, bag or {})
        return self.memo[key]

    def run(self, value) -> tuple:
        """``(slot, program)`` of a ``run`` of a program's name, ``(kind, program)`` or a call,
        bare or after its kind as ``(kind, call)``."""
        kind, ref = (value, value) if isinstance(value, (str, Call)) else value
        if isinstance(ref, Call):
            own = self.doc.forms.get(ref.name, (None,))[0]
            if own in (None, "restart"):
                raise PlayError(f"run: {ref}: {ref.name} is no ctrl, pw or filter program template")
            kind = own if kind is ref else kind
        return _slot(kind), self.program(ref, kind if isinstance(ref, Call) else None)

    def _fill(self, out: CProgram, source: Program, ref, bag=None):
        if source.loop is not None and source.loop >= len(source.rows):
            raise PlayError(f"program {ref}: loop row {source.loop} out of range")
        bag = bag or {}
        out.rows = tuple(
            (
                _ticks(_bound(r.wait, bag, ref), ref),
                _ticks(_bound(r.hold, bag, ref), ref),
                self.ops(_bound(r.keys, bag, ref)),
            )
            for r in source.rows
        )
        out.loop, out.fresh = source.loop, source.fresh

    def ops(self, keys) -> tuple:
        out = []
        for key, value in keys:
            key, value = _spelled(key, value, self.legacy)
            if key.rpartition(".")[2] in CELLS:
                key, value = self.cell(key, value, False)
            elif key == "run":
                value = self.run(value)
            out.append((key, value))
        return tuple(out)

    def cell(self, key: str, value, command: bool) -> tuple:
        """A modulated cell's key (``cell`` or ``slot.cell``) and value as ``(cell, (slot, fn))``,
        or for a stop ``(cell, (stop, the slots it ends))``. In the owning formats a row's
        modulator is the program slot 0 and a command's the command slot 1; ``hold`` ends the
        slots up to its own there, and every slot of the cell else."""
        name, _, cell = key.rpartition(".")
        if self.slots is None:
            if name:
                raise PlayError(f"{key}: a slot prefix is format {SLOTTED}'s")
            slot, ends = int(command), int(command) + 1
        else:
            names = self.slots[cell]
            if name and name not in names:
                raise PlayError(f"{key}: {cell} has no slot {name}")
            slot, ends = names.index(name) if name else 0, len(names)
        if value is None:
            return cell, (None, (slot,))
        if value == HOLD:
            return cell, (HOLD, tuple(range(ends)))
        return cell, (slot, self.expression(value, cell))

    def restart(self, ref) -> tuple | None:
        prog = self.program(ref, "restart")
        if prog is None or not prog.rows:
            return None
        ticks, ahead = [0], 0
        for (_, hold, _), (wait, _, _) in zip(prog.rows[-2::-1], prog.rows[:0:-1]):
            ahead += hold + wait
            ticks.append(ahead)
        return tuple(zip(prog.rows, reversed(ticks)))

    def shape(self, fn):
        if fn is None:
            return None
        if callable(fn):
            return fn
        if fn not in SHAPES:
            raise PlayError(f"unknown modulator {fn}")
        return SHAPES[fn]

    def instrument(self, name: str | None) -> CInstrument | None:
        if name is None:
            return None
        given = not isinstance(name, str)
        key = ("ins", id(name) if given else name)
        if key not in self.memo:
            ins = name if given else self.named("instrument", self.doc.instruments, name)
            self.memo[key] = CInstrument(
                ins.ad,
                ins.sr,
                self.program(ins.ctrl, "ctrl"),
                self.program(ins.pw, "pw", True),
                self.program(ins.filter, "filter", True),
                tuple(self.cell(key, value, False)[1] for key, value in ins.freq) or None,
                self.shape(ins.fn),
                dict(ins.p0) if ins.p0 else None,
                self.restart(ins.restart),
            )
        return self.memo[key]

    def expression(self, value, cell: str):
        """The modulator a binding of ``cell`` names: an integer, an expression, a call, or
        before format 1.23 an instance's or a template's name or a template with its parameter
        bag; a code template's is its :class:`Routine`."""
        freq = cell == "freq"
        if isinstance(value, int):
            return self.fn(Expr(ast.Constant(value)), freq)
        if isinstance(value, Call):
            if not all(isinstance(a, int) for a in value.args):
                raise PlayError(f"{value}: a parameter is read only in a program template's rows")
            return self.call(value, cell)
        if isinstance(value, Code):
            return self.anonymous(value, cell)
        if self.calls:
            return self._uncalled(value, cell)
        if isinstance(value, str):
            if value in self.doc.expressions:
                return self.expression(self.doc.expressions[value], cell)
            self.named("modulator", self.doc.templates, value)
            value = Expr(ast.Name(value))
        node = value.node
        if isinstance(node, ast.Name) and node.id in self.doc.templates:
            node = self.doc.templates[node.id].node
            value = Expr(value.node, value.bag or ())
        elif value.bag is not None:
            raise PlayError(f"{ast.unparse(node)}: a parameter bag binds a template")
        if value.bag is not None:
            if not all(isinstance(x, int) for _, x in value.bag):
                raise PlayError(f"{ast.unparse(value.node)}: a parameter is an integer")
            try:
                node = bind(node, dict(value.bag))
            except ExpressionError as exc:
                raise PlayError(f"{ast.unparse(value.node)}: {exc}") from exc
        return self.fn(Expr(node), freq)

    def _uncalled(self, value, cell: str):
        """Format 1.23's binding that is no call: an expression that names no template and has
        no bag."""
        node = value.node if isinstance(value, Expr) else None
        if (
            node is None
            or value.bag is not None
            or self.doc.templates.keys() & {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        ):
            text = value if node is None else ast.unparse(node)
            raise PlayError(f"{text}: format {VERSION} binds a template by a call, name(1,-2)")
        return self.fn(value, cell == "freq")

    def call(self, value: Call, cell: str):
        """The modulator of a call: its template with each declared parameter its argument,
        memoised by the template, the cell and the arguments."""
        if not self.calls:
            raise PlayError(f"{value}: a call is format {VERSION}'s")
        doc = self.doc
        template = self.named("modulator", doc.templates, value.name)
        declared = doc.signatures[value.name]
        if len(declared) != len(value.args):
            raise PlayError(f"{value}: {value.name} takes {len(declared)} argument(s)")
        bag, key = dict(zip(declared, value.args)), ("call", value, cell)
        if not isinstance(template, Code):
            return self.fn(Expr(bind(template.node, bag)), cell == "freq", key)
        if key not in self.memo:
            head = self.doc.header
            self.machine = self.machine or Machine(doc.tables, head.state, head.clock_hz or PAL_HZ)
            self.memo[key] = self.machine.compile(value.name, template.statements, bag, cell)
        return self.memo[key]

    def anonymous(self, value: Code, cell: str) -> Routine:
        """An anonymous code body's routine, memoised by its one text and the cell."""
        text = inline(value.statements)
        if not self.calls:
            raise PlayError(f"{text}: code is format {VERSION}'s")
        if code_params(value.statements):
            raise PlayError(f"{text}: an anonymous body reads no parameter")
        key, head = ("asm", text, cell), self.doc.header
        if key not in self.memo:
            check(value.statements, {n for names in head.state.values() for n, _ in names})
            self.machine = self.machine or Machine(
                self.doc.tables, head.state, head.clock_hz or PAL_HZ
            )
            self.memo[key] = self.machine.compile(text, value.statements, {}, cell)
        return self.memo[key]

    def defaults(self) -> dict:
        """``{cell: ((slot, fn), ...)}`` of the slots' default bindings."""
        return {
            cell: tuple((k, self.expression(s.mod, cell)) for k, s in enumerate(slots) if s.mod)
            for cell, slots in (self.slotted or {}).items()
        }

    def event(self, event) -> CEvent:
        cmds = dict(event.commands)
        ops = []
        for key, value in event.commands:
            key, value = _spelled(key, value, self.legacy)
            if key == "run":
                value = None if value is None else self.run(value)
            elif key.rpartition(".")[2] in CELLS:
                key, value = self.cell(key, value, True)
            elif key in ("tempo", "vtempo", "legato"):
                continue
            ops.append((key, value))
        note = event.note
        if note == "-":
            note = None
        elif note not in ("off", "on"):
            note = note_number(note)
        return CEvent(
            note,
            self.instrument(event.ins),
            tuple(ops),
            event.keep_pw,
            event.keep_filter,
            event.steps,
            "legato" in cmds,
            _tempo(cmds["tempo"], "!tempo") if "tempo" in cmds else None,
            _tempo(cmds["vtempo"], "!vtempo") if "vtempo" in cmds else None,
        )

    def phrase(self, name: str) -> tuple:
        given = not isinstance(name, str)
        key = ("phrase", id(name) if given else name)
        if key not in self.memo:
            events = name if given else self.named("phrase", self.doc.phrases, name)
            self.memo[key] = tuple(self.event(e) for e in events)
        return self.memo[key]

    def entry(self, entry) -> tuple:
        if entry.kind == "phrase":
            return ("phrase", self.phrase(entry.phrase), entry.transpose)
        return (entry.kind,)


def compile_document(doc: Document, compiler=None) -> Song:
    """The Song of a Document: every name resolved and every expression compiled once, by
    ``compiler``, a ``_Compiler`` subclass when given."""
    head = doc.header
    if head.version not in (*OWNED, SLOTTED, VERSION):
        raise PlayError(
            f"version {head.version}: the player reads {', '.join((*OWNED, SLOTTED))} and {VERSION}"
        )
    if not 1 <= head.voices <= MAX_VOICES:
        raise PlayError(f"voices: {head.voices}: the player plays 1 to {MAX_VOICES} voices")
    clock_hz = head.clock_hz or PAL_HZ
    comp = (compiler or _Compiler)(doc, note_table(clock_hz))
    for name in doc.programs:
        comp.program(name)
    for name in doc.instruments:
        comp.instrument(name)
    init = tuple(comp.event(e) for e in doc.init or ())
    if doc.init is not None and len(init) != head.voices:
        raise PlayError(f"init: {len(init)} events for {head.voices} voices")
    timeline = []
    for i, plays in enumerate(doc.timeline):
        if len(plays) != head.voices:
            raise PlayError(f"row {i}: {len(plays)} play entries for {head.voices} voices")
        timeline.append(tuple(comp.entry(p) for p in plays))
    if doc.loop is not None and doc.loop >= len(timeline):
        raise PlayError(f"song: loop row {doc.loop} out of range")
    instruments = [v for k, v in comp.memo.items() if k[0] == "ins"]
    lookahead = max((r[0][1] for i in instruments if (r := i.restart)), default=0)
    marks = _marks(head.hold, comp.slotted)
    earliest = max((k for events in marks.values() for _, k in events), default=0)
    return Song(
        head.voices,
        _tempo(head.tempo, "tempo"),
        clock_hz,
        init,
        tuple(timeline),
        doc.loop,
        lookahead,
        comp.table,
        marks,
        {
            mark: tuple(e for e, _ in events if e.startswith("row"))
            for mark, events in marks.items()
        },
        lookahead + earliest,
        comp.slots,
        comp.defaults(),
        comp.order,
        {cell: sum(size for _, size in names) for cell, names in head.state.items()},
        comp.machine is not None,
    )


def _marks(hold: dict, slots: dict | None) -> dict:
    """``{mark: events}``: ``hold key`` for each key of the hold line in the owning formats
    (``ctrl.cell`` a control row's modulators), else ``hold slot.cell`` and ``reset slot.cell``
    for each slot."""
    marks = {f"hold {key}": events for key, events in hold.items()}
    for cell, cells in (slots or {}).items():
        for slot in cells:
            marks[f"hold {slot.name}.{cell}"] = slot.hold
            marks[f"reset {slot.name}.{cell}"] = slot.reset
    for mark, events in marks.items():
        for event, _ in events:
            if event.startswith("row.") and event[4:] not in ROW_KEYS:
                raise PlayError(f"{mark}: {event}: no row key {event[4:]}")
    return {mark: frozenset(events) for mark, events in marks.items() if events}


def load(text: str) -> Song:
    """The Song of canonical document ``text``."""
    return compile_document(read(text))
