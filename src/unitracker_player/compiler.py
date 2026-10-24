"""A Document compiled for playing: its names resolved and its programs, instruments and
modulators compiled once."""

import ast
from dataclasses import dataclass

from unitracker_grammar import PAL_HZ
from unitracker_grammar.code import Call, inline, signature
from unitracker_grammar.expr import bind, tree
from unitracker_grammar.prelude import PRELUDE

from unitracker_player.code import Machine, Routine
from unitracker_player.document import (
    HOLD,
    RESET,
    Code,
    Document,
    Expr,
    Once,
    Param,
    Program,
    Row,
    Slot,
    note_number,
    read,
)
from unitracker_player.expr import compile_expr, constant, note_table, offset
from unitracker_player.sequencer import PlayError

MAX_VOICES = 3
REGISTERS = 25
ENVELOPE = ("ad", "sr", "ctrl")
CHIP = ("cut", "res", "route", "mode", "vol")
CELLS = ("pw", "cut", "freq")
CONTROL, PULSE, FILTER = range(3)
ORDER = ("events", "ctrl", "pulse", "filter", "freq", "pw", "cut")
PHASES = (*ORDER, "read")
SLOT_PHASES = {"ctrl": CONTROL, "pulse": PULSE, "filter": FILTER}
FREQ_MASK, PW_MASK, CUT_MASK = 0xFFFF, 0xFFF, 0x7FF
KIND_CELL = {"pw": "pw", "filter": "cut"}
ROW_KEYS = frozenset("ctrl gate note ad sr pw cut res route mode freq run vol".split())
PRELUDE_TEMPLATES = {
    (sig := signature(key))[0]: (sig[1], Expr(tree(text))) for key, text in PRELUDE.items()
}


@dataclass(eq=False)
class CProgram:
    """A compiled program: rows of ``(wait, hold, ops)`` and the loop row, or None for end;
    filled after it is memoised, so a program may run itself."""

    rows: tuple = ()
    loop: int | None = None
    fresh: bool = False


@dataclass(frozen=True)
class CInstrument:
    """A compiled instrument; ``freq`` its ``(slot, fn)`` frequency modulators or None, and
    ``restart`` its rows with the ticks each precedes the note."""

    ad: int | None
    sr: int | None
    ctrl: CProgram | None
    pw: CProgram | None
    filter: CProgram | None
    freq: object
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
class Song:  # pylint: disable=too-many-instance-attributes
    """A document compiled for playing: ``slots`` ``{cell: slot names}``, ``marks`` ``{mark:
    events}`` per ``hold``/``reset slot.cell``, ``rows`` their row events, ``defaults`` ``{cell:
    ((slot, fn, once), ...)}``, ``state`` ``{cell: bytes}``, ``code`` whether code plays, ``regs``
    the registers before tick 0, ``lag`` the ticks they are late, ``shared`` the song's cells."""

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
    slots: dict
    defaults: dict
    order: tuple
    state: dict
    code: bool
    regs: tuple
    lag: int = 0
    shared: tuple = ()


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


def _bound(value, bag: dict):
    """A program row's value with each parameter of a template's that it reads, a Param, a
    cell value that is its name alone or a call's named argument, its integer in ``bag``."""
    if isinstance(value, Param):
        return bag[value.name] * value.sign
    if isinstance(value, Expr) and isinstance(value.node, ast.Name) and value.node.id in bag:
        return bag[value.node.id]
    if isinstance(value, Call):
        args = tuple(a if isinstance(a, int) else bag[a] for a in value.args)
        return Call(value.name, args)
    if isinstance(value, tuple):
        return tuple(_bound(v, bag) for v in value)
    return value


def _ticks(value: int, where) -> int:
    if value < 0:
        raise PlayError(f"program {where}: {value} ticks")
    return value


class _Compiler:
    """Resolves a Document's names and compiles its programs, instruments and expressions, each
    once; each method compiles the construct of its name."""

    # pylint: disable=missing-function-docstring

    def __init__(self, doc: Document, table):
        self.doc, self.table = doc, table
        head = doc.header
        self.memo: dict = {}
        self.machine = None
        self.slotted = {c: head.slots.get(c, (Slot(c),)) for c in CELLS}
        self.slots = {c: tuple(s.name for s in slots) for c, slots in self.slotted.items()}
        for cell, names in self.slots.items():
            if len(set(names)) != len(names):
                raise PlayError(f"slots: {cell} names a slot twice")
        self.order = ORDER if head.order is None else head.order
        if head.order is not None and sorted(head.order) != sorted(PHASES):
            raise PlayError(f"order: each of {', '.join(PHASES)} once")

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
        source = self.named("program", self.doc.programs, ref) if isinstance(ref, str) else ref
        fresh = fresh and (isinstance(source, Call) or source.binding)
        key = ("program", ref if isinstance(ref, str) else id(ref), fresh)
        if key not in self.memo:
            if isinstance(source, Call):
                self.memo[key] = self.program(source, _kind(ref), fresh)
                return self.memo[key]
            out = self.memo[key] = CProgram()
            self._fill(out, source, ref)
            out.fresh = out.fresh or fresh
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
                self.memo[key] = self.program(_bound(body, dict(zip(declared, ref.args))), own)
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
                _ticks(_bound(r.wait, bag), ref),
                _ticks(_bound(r.hold, bag), ref),
                self.ops(_bound(r.keys, bag)),
            )
            for r in source.rows
        )
        out.loop, out.fresh = source.loop, source.fresh

    def ops(self, keys) -> tuple:
        out = []
        for key, value in keys:
            if key.rpartition(".")[2] in CELLS:
                key, value = self.cell(key, value)
            elif key == "run":
                value = self.run(value)
            out.append((key, value))
        return tuple(out)

    def cell(self, key: str, value) -> tuple:
        """A modulated cell's key (``cell`` or ``slot.cell``) and value as ``(cell, (slot, fn,
        once))``, or for a stop ``(cell, (stop, the slots it acts on))``: ``none`` and ``reset``
        its slot, ``hold`` every slot of the cell."""
        name, _, cell = key.rpartition(".")
        names = self.slots[cell]
        if name and name not in names:
            raise PlayError(f"{key}: {cell} has no slot {name}")
        slot = names.index(name) if name else 0
        if value is None or value == RESET:
            return cell, (value, (slot,))
        if value == HOLD:
            return cell, (HOLD, tuple(range(len(names))))
        once = isinstance(value, Once)
        return cell, (slot, self.expression(value.value if once else value, cell), once)

    def restart(self, ref) -> tuple | None:
        prog = self.program(ref, "restart")
        if prog is None or not prog.rows:
            return None
        ticks, ahead = [0], 0
        for (_, hold, _), (wait, _, _) in zip(prog.rows[-2::-1], prog.rows[:0:-1]):
            ahead += hold + wait
            ticks.append(ahead)
        return tuple(zip(prog.rows, reversed(ticks)))

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
                tuple(self.cell(key, value)[1] for key, value in ins.freq) or None,
                self.restart(ins.restart),
            )
        return self.memo[key]

    def expression(self, value, cell: str):
        """The modulator a binding of ``cell`` names: an integer, a call, a code body, or an
        expression that names no template; a code template's is its :class:`Routine`."""
        freq = cell == "freq"
        if isinstance(value, int):
            return self.fn(Expr(ast.Constant(value)), freq)
        if isinstance(value, Call):
            return self.call(value, cell)
        if isinstance(value, Code):
            return self.anonymous(value, cell)
        return self.fn(value, freq)

    def call(self, value: Call, cell: str):
        """The modulator of a call: its template with each declared parameter its argument,
        memoised by the template, the cell and the arguments."""
        doc = self.doc
        if value.name not in doc.templates and value.name in PRELUDE_TEMPLATES:
            declared, template = PRELUDE_TEMPLATES[value.name]
        else:
            template = self.named("modulator", doc.templates, value.name)
            declared = doc.signatures[value.name]
        if len(declared) != len(value.args):
            raise PlayError(f"{value}: {value.name} takes {len(declared)} argument(s)")
        bag, key = dict(zip(declared, value.args)), ("call", value, cell)
        if not isinstance(template, Code):
            return self.fn(Expr(bind(template.node, bag)), cell == "freq", key)
        if key not in self.memo:
            self.machine = self.machine or self._machine()
            self.memo[key] = self.machine.compile(value.name, template.statements, bag, cell)
        return self.memo[key]

    def _machine(self) -> Machine:
        head = self.doc.header
        return Machine(self.doc.tables, head.state, head.clock_hz or PAL_HZ, head.rounding)

    def anonymous(self, value: Code, cell: str) -> Routine:
        """An anonymous code body's routine, memoised by its one text and the cell."""
        text = inline(value.statements)
        key = ("asm", text, cell)
        if key not in self.memo:
            self.machine = self.machine or self._machine()
            self.memo[key] = self.machine.compile(text, value.statements, {}, cell)
        return self.memo[key]

    def defaults(self) -> dict:
        """``{cell: ((slot, fn), ...)}`` of the slots' default bindings."""
        return {
            cell: tuple(
                (k, self.expression(s.mod, cell), False) for k, s in enumerate(slots) if s.mod
            )
            for cell, slots in self.slotted.items()
        }

    def event(self, event) -> CEvent:
        cmds = dict(event.commands)
        ops = []
        for key, value in event.commands:
            if key == "run":
                value = None if value is None else self.run(value)
            elif key.rpartition(".")[2] in CELLS:
                key, value = self.cell(key, value)
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
            _tempo(cmds["tempo"], "tempo") if "tempo" in cmds else None,
            _tempo(cmds["vtempo"], "vtempo") if "vtempo" in cmds else None,
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
    if not 1 <= head.voices <= MAX_VOICES:
        raise PlayError(f"voices: {head.voices}: the player plays 1 to {MAX_VOICES} voices")
    clock_hz = head.clock_hz or PAL_HZ
    comp = (compiler or _Compiler)(doc, note_table(clock_hz, head.rounding))
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
    marks = _marks(comp.slotted)
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
        _regs(head.regs),
        head.lag,
        head.global_cells,
    )


def _regs(regs: tuple | None) -> tuple:
    """The registers before tick 0, each a byte: all 0 where the document gives none."""
    regs = regs or (0,) * REGISTERS
    if len(regs) != REGISTERS:
        raise PlayError(f"regs: {len(regs)} values for {REGISTERS} registers")
    return tuple(r & 0xFF for r in regs)


def _marks(slots: dict) -> dict:
    """``{mark: events}``: ``hold slot.cell`` and ``reset slot.cell`` for each slot."""
    marks = {}
    for cell, cells in slots.items():
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
