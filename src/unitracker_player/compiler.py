"""A Document compiled for playing: its names resolved and its programs, instruments and
modulators compiled once."""

import ast
import re
from dataclasses import dataclass
from functools import cached_property

from unitracker_grammar import PAL_HZ
from unitracker_grammar.cells import CELLS, LATER_CELLS, MASKS, PHASE_OF
from unitracker_grammar.code import Call, inline, signature
from unitracker_grammar.expr import bind, tree
from unitracker_grammar.model import (
    CONT,
    END,
    HOLD,
    LAYERED_KINDS,
    NONE,
    RESET,
    Code,
    Document,
    Expr,
    Once,
    Program,
    Row,
    base,
)
from unitracker_grammar.prelude import PRELUDE

from unitracker_player.code import Machine, Routine
from unitracker_player.document import read
from unitracker_player.expr import compile_expr, constant, note_table, offset
from unitracker_player.sequencer import PlayError, loops

MAX_VOICES = 3
REGISTERS = 25
ENVELOPE = ("ad", "sr", "ctrl")
CONTROL, PULSE, FILTER = range(3)
SLOT_PHASES = {"ctrl": CONTROL, "pulse": PULSE, "filter": FILTER}
KIND_SLOT = {"ctrl": CONTROL, "pw": PULSE, "filter": FILTER}
SLOTS = tuple(KIND_SLOT.values())
KIND_CELL = {"pw": "pw", "filter": "cut"}
CELL_OF = {phase: cell for cell, phase in PHASE_OF.items()}
SEQUENCE = ("cut", "ctl", "ad", "sr", "res", "route", "mode", "vol")
DEFAULT_ORDER = ("events", "ctrl", "pulse", "filter", "freq", "pw", "cut", *SEQUENCE[1:])
REQUIRED = frozenset("events ctrl pulse filter freq pw cut read".split())
STOPS = (NONE, HOLD, RESET)
ROW_KEYS = frozenset("ctrl gate note ad sr pw cut res route mode freq run vol".split())
EVENT_KEY = {"mask": "ctrl", "route_on": "route", "route_off": "route"}
PRELUDE_TEMPLATES = {
    (sig := signature(key))[0]: (sig[1], Expr(text)) for key, text in PRELUDE.items()
}
_NOTE = re.compile(r"([A-G])(#?)(-?\d+)")
_PITCH = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def note_number(name: str) -> int:
    """The format note of a note name: ``12*octave + pitch class`` (C2 is 24, B-1 is -1)."""
    letter, sharp, octave = _NOTE.fullmatch(name).groups()
    return 12 * int(octave) + _PITCH[letter] + (1 if sharp else 0)


@dataclass(eq=False)
class CProgram:
    """A compiled program: rows of ``(wait, hold, ops)`` and the loop row, or None for end;
    filled after it is memoised, so a program may run itself."""

    rows: tuple = ()
    loop: int | None = None
    fresh: bool = False


@dataclass(frozen=True)
class CInstrument:  # pylint: disable=too-many-instance-attributes
    """A compiled instrument. ``binds`` are its ``(cell, op)`` bindings of ``freq``, ``ad`` and
    ``sr``, ``restart`` its rows with the ticks each precedes the note, ``layers`` the ``(program
    slot, program)`` of the layers past each kind's first."""

    ad: int | None
    sr: int | None
    ctrl: CProgram | None
    pw: CProgram | None
    filter: CProgram | None
    binds: tuple
    restart: tuple | None
    layers: tuple = ()


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
    """A document compiled for playing; see docs/semantics.md for the fields' meanings.
    ``marks`` are the events a slot holds or resets on and a program slot pauses on, ``rows``
    their row events, ``shared`` the song's cells, ``frame`` and ``full`` the calls of an output
    row and the pattern of which are full (None: all); the sequencer's ticks are the full calls."""

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
    kinds: tuple = (CONTROL, PULSE, FILTER)
    orders: tuple | None = None
    frame: int = 1
    full: tuple | None = None

    @property
    def looping(self) -> bool:
        """Whether the song plays forever: its rows loop, or an orderlist does."""
        if self.orders is None:
            return self.loop is not None
        return any(loop is not None for _, loop in self.orders)

    @cached_property
    def periods(self) -> tuple | None:
        """Per voice of an orderlist song ``(loop entry, tick, period)``: its events from
        ``tick`` on repeat every ``period`` ticks; None for a voice that ends, and for rows."""
        return None if self.orders is None else loops(self)

    @cached_property
    def _ones(self) -> tuple:
        return tuple(i for i, full in enumerate(self.full or ()) if full)

    def call_of(self, tick: int) -> int:
        """The call that is sequencer tick ``tick``, the ``tick``th full call."""
        if self.full is None:
            return tick
        cycle, at = divmod(tick, len(self._ones))
        return cycle * len(self.full) + self._ones[at]


def _tempo(value, where: str) -> tuple:
    pair = value if isinstance(value, tuple) else (value, value)
    if min(pair) < 1:
        raise PlayError(f"{where}: a tempo of 0 ticks per step")
    return pair


def _kind(name: str) -> str:
    """The kind of a program's name or an inline program's kind."""
    return {"r": "restart", "c": "ctrl", "p": "pw", "f": "filter"}[name[0]]


def _program(value) -> bool:
    """Whether a pulse or filter field's value is a program or its name."""
    return isinstance(value, Program) or isinstance(value, str) and value not in STOPS


def _bound(value, bag: dict):
    """A program row's value with each parameter of a template that it reads, a name of ``bag``
    as a value or a call's argument or a relative note's ``+name``, ``-name``, its integer."""
    if isinstance(value, str):
        if value in bag:
            return bag[value]
        if value[1:] in bag and value[0] in "+-":
            return bag[value[1:]] * (-1 if value[0] == "-" else 1)
    elif isinstance(value, Expr):
        return bag.get(value.text, value)
    elif isinstance(value, Call):
        return Call(value.name, tuple(a if isinstance(a, int) else bag[a] for a in value.args))
    elif isinstance(value, Once):
        return Once(_bound(value.value, bag))
    return value


def _ticks(value: int, where) -> int:
    if value < 0:
        raise PlayError(f"program {where}: {value} ticks")
    return value


def _events(events) -> tuple:
    """``((event, k), ...)`` of a slot's hold or reset events ``step-1``, ``row.freq``."""
    return tuple((name, int(k or 0)) for name, _, k in (e.partition("-") for e in events or ()))


def _phases(order: tuple | None) -> tuple:
    """The phases of a tick: ``order`` with each phase of the sequence ``cut ctl ad sr res route
    mode vol`` that it leaves out after the one before it in the sequence."""
    if order is None:
        return DEFAULT_ORDER
    if (
        REQUIRED - set(order)
        or set(order) - REQUIRED - set(SEQUENCE)
        or len(set(order)) < len(order)
    ):
        raise PlayError(
            f"order: each of {', '.join(sorted(REQUIRED))} once, the cells' phases once"
        )
    out = list(order)
    for before, phase in zip(SEQUENCE, SEQUENCE[1:]):
        if phase not in out:
            out.insert(out.index(before) + 1, phase)
    return tuple(out)


class _Compiler:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """Resolves a Document's names and compiles its programs, instruments and expressions, each
    once; each method compiles the construct of its name."""

    # pylint: disable=missing-function-docstring

    def __init__(self, doc: Document, table):
        self.doc, self.table = doc, table
        head = doc.header
        self.tables = dict(doc.tables)
        self.memo: dict = {}
        self.machine = None
        self.instruments = doc.records("ins")
        self.phrases = doc.records("phrase")
        self._register(doc)
        self.slotted = {c: head.slot(c) for c in CELLS}
        self.slots = {c: tuple(s.name for s in slots) for c, slots in self.slotted.items()}
        for cell, names in self.slots.items():
            if len(set(names)) != len(names):
                raise PlayError(f"slots: {cell} names a slot twice")
        self.order = _phases(head.order)
        self.layers, self.kinds = self._layering(head)

    def _register(self, doc: Document):
        """The named programs, the program templates and the modulator templates of ``doc``."""
        self.programs, self.forms = {}, {}
        for kind in ("restart", "ctrl", "pw", "filter"):
            for key, value in doc.records(kind).items():
                if "(" not in key:
                    self.programs[key] = value
                    continue
                name, declared = signature(key)
                if name in self.forms:
                    raise PlayError(f"program template {name} is defined twice")
                self.forms[name] = (kind, declared, value)
        self.templates, self.signatures = {}, {}
        for key, body in doc.mods:
            name, declared = signature(key)
            self.templates[name], self.signatures[name] = body, declared

    def _layering(self, head) -> tuple:
        """``({kind: (layer names, program slots)}, the kind of each program slot)``."""
        kinds, layers = [CONTROL, PULSE, FILTER], {}
        named = dict(head.layers or ())
        for kind, slot in KIND_SLOT.items():
            names = named.get(kind, ())
            if len(set(names)) != len(names):
                raise PlayError(f"layers: {kind} names a layer twice")
            if clash := sorted(set(names) & set(self.slots.get(KIND_CELL.get(kind), ()))):
                raise PlayError(f"layers: {clash} name both a {kind} layer and a slot")
            layers[kind] = (names, (slot, *range(len(kinds), len(kinds) + len(names) - 1)))
            kinds += [slot] * (len(names) - 1)
        return layers, tuple(kinds)

    def layer(self, kind: str, name: str = "") -> int:
        """The program slot of ``kind``'s layer ``name``, its first where ``name`` is empty."""
        names, slots = self.layers[kind]
        if name and name not in names:
            raise PlayError(f"{name}.{kind}: {kind} has no layer {name}")
        return slots[names.index(name) if name else 0]

    def pauses(self) -> dict:
        """``{program slot: ((event, k), ...)}`` of the ``holds`` line: a kind's key holds every
        layer of it, a layer's key that layer, a slot on the union of its keys' events."""
        out: dict = {}
        for key, events in self.doc.header.holds or ():
            if key in KIND_SLOT:
                slots = self.layers[key][1]
            else:
                slots = [
                    self.layer(k, key) for k, (names, _) in self.layers.items() if key in names
                ]
            for slot in slots:
                out[slot] = (*out.get(slot, ()), *_events(events))
        return out

    def fn(self, node: ast.expr, freq: bool = False, key=None):
        """The compiled expression, memoised by its node or by ``key``."""
        if freq and constant(node) is not None:
            node = offset(constant(node))
        key = ("fn", ast.dump(node)) if key is None else key
        if key not in self.memo:
            try:
                self.memo[key] = compile_expr(node, self.table, self.tables)
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
        source = self.named("program", self.programs, ref) if isinstance(ref, str) else ref
        program = isinstance(source, Program)
        fresh = fresh and not program
        key = ("program", ref if isinstance(ref, str) else id(ref), fresh)
        if key not in self.memo:
            if isinstance(source, Call):
                self.memo[key] = self.program(source, _kind(ref), fresh)
                return self.memo[key]
            out = self.memo[key] = CProgram()
            if program:
                self._fill(out, source.rows, source.loop, False, ref)
            else:
                rows = (Row(((KIND_CELL[_kind(ref)], source),)),)
                self._fill(out, rows, None, fresh, ref)
        return self.memo[key]

    def form(self, ref: Call, kind: str | None, fresh: bool) -> CProgram:
        """The program of a call: its template's with each parameter its argument, memoised by
        the call (a call of a modulator template where a pw or filter field binds one)."""
        if ref.name not in self.forms:
            if kind not in KIND_CELL:
                raise PlayError(f"{ref}: {ref.name} is no program template")
            key = ("program", ref, kind, fresh)
            if key not in self.memo:
                rows = (Row(((KIND_CELL[kind], ref),)),)
                self._fill(self.memo.setdefault(key, CProgram()), rows, None, fresh, ref)
            return self.memo[key]
        own, declared, body = self.forms[ref.name]
        if kind not in (None, own):
            raise PlayError(f"{ref}: {ref.name} is a {own} program, not a {kind} program")
        if len(declared) != len(ref.args) or not all(isinstance(a, int) for a in ref.args):
            raise PlayError(f"{ref}: {ref.name} takes {len(declared)} integer argument(s)")
        key, bag = ("program", ref), dict(zip(declared, ref.args))
        if isinstance(body, Call):
            if key not in self.memo:
                self.memo[key] = None
                self.memo[key] = self.program(_bound(body, bag), own)
            if self.memo[key] is None:
                raise PlayError(f"{ref}: a program template calls itself")
            return self.memo[key]
        if key not in self.memo:
            out = self.memo.setdefault(key, CProgram())
            self._fill(out, body.rows, body.loop, False, ref, bag)
        return self.memo[key]

    def run(self, key: str, value) -> tuple:
        """``(slot, program)`` of a ``run`` (``layer.run``) of a program's name, ``(kind,
        program)`` or a call, bare or after its kind as ``(kind, call)``; ``none`` the control
        layer's, the program None."""
        layer = key.rpartition(".")[0]
        if value == NONE:
            return self.layer("ctrl", layer), None
        kind, ref = (value, value) if isinstance(value, (str, Call)) else value
        if isinstance(ref, Call):
            own = self.forms.get(ref.name, (None,))[0]
            if own in (None, "restart"):
                raise PlayError(f"run: {ref}: {ref.name} is no ctrl, pw or filter program template")
            kind = own if kind is ref else kind
        prog = self.program(ref, kind if isinstance(ref, Call) else None)
        return self.layer(_kind(kind), layer), prog

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def _fill(self, out: CProgram, rows: tuple, loop, fresh: bool, ref, bag=None):
        if loop is not None and loop >= len(rows):
            raise PlayError(f"program {ref}: loop row {loop} out of range")
        out.rows = tuple(self._row(r, bag or {}, ref) for r in rows)
        out.loop, out.fresh = loop, fresh

    def _row(self, row: Row, bag: dict, ref) -> tuple:
        keys = [(k, _bound(v, bag)) for k, v in row.keys] if bag else list(row.keys)
        timing = {k: v for k, v in keys if k in ("wait", "hold")}
        rest = [(k, v) for k, v in keys if k not in timing]
        wait, hold = timing.get("wait", 0), timing.get("hold", 1)
        return _ticks(wait, ref), _ticks(hold, ref), self.ops(rest)

    def ops(self, keys) -> tuple:
        out = []
        for key, value in keys:
            name = base(key)
            if name in CELLS:
                out.append(self.cell(key, value))
            elif name == "run":
                out.append(("run", self.run(key, value)))
            elif name == "note":
                absolute = isinstance(value, str)
                out.append(("note", ("abs", note_number(value)) if absolute else ("rel", value)))
            else:
                out.append((key, int(value)))
        return tuple(out)

    def cell(self, key: str, value) -> tuple:
        """A cell's key (``cell`` or ``slot.cell``) and value as ``(cell, op)``: for a plain
        integer of a cell that takes one the integer wrapped to the cell's mask, for a binding
        ``(slot, fn, once)``, for a stop ``(stop, the slots it acts on)``: ``none`` and ``reset``
        its slot, ``hold`` every slot of the cell."""
        name, _, cell = key.rpartition(".")
        names = self.slots[cell]
        if name and name not in names:
            raise PlayError(f"{key}: {cell} has no slot {name}")
        slot = names.index(name) if name else 0
        if cell in LATER_CELLS and not name and isinstance(value, int):
            return cell, int(value) & MASKS[cell]
        if value in (NONE, RESET):
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

    def _modulation(self, kind: str, fields: list):
        """A pulse or filter field group as the program it stands for: a program, its name or a
        call alone and unprefixed is itself, else one row binding each field's slot of the
        cell, its bindings restarting phi on each note."""
        if len(fields) == 1 and fields[0][0] == kind:
            if isinstance(fields[0][1], Call) or _program(fields[0][1]):
                return fields[0][1]
        if any(_program(value) for _, value in fields):
            raise PlayError(f"{kind}: a program is the one {kind} field and takes no slot")
        out = CProgram()
        keys = tuple((k[: -len(kind)] + KIND_CELL[kind], v) for k, v in fields)
        self._fill(out, (Row(keys),), None, True, kind)
        return out

    def instrument(self, name: str | None) -> CInstrument | None:
        if name is None:
            return None
        given = not isinstance(name, str)
        key = ("ins", id(name) if given else name)
        if key not in self.memo:
            ins = name if given else self.named("instrument", self.instruments, name)
            self.memo[key] = self._instrument(ins)
        return self.memo[key]

    def _instrument(self, ins) -> CInstrument:
        sets, binds, groups, restart = {}, [], {}, None
        for key, value in ins.fields:
            prefix, _, kind = key.rpartition(".")
            if kind in LAYERED_KINDS:
                names = self.layers[kind][0]
                if kind == "ctrl" or prefix in names:
                    key, prefix = kind, "" if prefix in names[:1] else prefix
                groups.setdefault((kind, "" if key != kind else prefix), []).append((key, value))
            elif kind == "restart":
                restart = value
            else:
                op = self.cell(key, value)[1]
                if isinstance(op, int):
                    sets[kind] = op
                else:
                    binds.append((kind, op))
        programs, layers = self._programs(groups)
        return CInstrument(
            sets.get("ad"),
            sets.get("sr"),
            programs.get("ctrl"),
            programs.get("pw"),
            programs.get("filter"),
            tuple(binds),
            self.restart(restart),
            tuple(layers),
        )

    def _programs(self, groups: dict) -> tuple:
        """``({kind: first layer's program}, ((program slot, program), ...))`` of an
        instrument's program fields grouped by ``(kind, layer)``."""
        programs, layers = {}, []
        for (kind, layer), own in groups.items():
            if kind == "ctrl" and len(own) > 1:
                raise PlayError(f"{layer or kind}: an instrument starts a layer twice")
            ref = own[0][1] if kind == "ctrl" else self._modulation(kind, own)
            program = ref if isinstance(ref, CProgram) else self.program(ref, kind, kind != "ctrl")
            if layer:
                layers.append((self.layer(kind, layer), program))
            else:
                programs[kind] = program
        return programs, layers

    def expression(self, value, cell: str):
        """The modulator a binding of ``cell`` names: an integer, a call, a code body, or an
        expression that names no template; a code template's is its :class:`Routine`."""
        if isinstance(value, int):
            return self.fn(offset(int(value)) if cell == "freq" else ast.Constant(int(value)))
        if isinstance(value, Call):
            return self.call(value, cell)
        if isinstance(value, Code):
            return self.anonymous(value, cell)
        return self.fn(tree(value.text), cell == "freq")

    def call(self, value: Call, cell: str):
        """The modulator of a call: its template with each declared parameter its argument,
        memoised by the template, the cell and the arguments."""
        if value.name not in self.templates and value.name in PRELUDE_TEMPLATES:
            declared, template = PRELUDE_TEMPLATES[value.name]
        else:
            template = self.named("modulator", self.templates, value.name)
            declared = self.signatures[value.name]
        if len(declared) != len(value.args):
            raise PlayError(f"{value}: {value.name} takes {len(declared)} argument(s)")
        bag, key = dict(zip(declared, value.args)), ("call", value, cell)
        if not isinstance(template, Code):
            return self.fn(bind(tree(template.text), bag), cell == "freq", key)
        if key not in self.memo:
            self.machine = self.machine or self._machine()
            self.memo[key] = self.machine.compile(value.name, template.statements, bag, cell)
        return self.memo[key]

    def _machine(self) -> Machine:
        head = self.doc.header
        clock_hz = head.clock[1] if head.clock else PAL_HZ
        return Machine(self.tables, dict(head.state or ()), clock_hz)

    def anonymous(self, value: Code, cell: str) -> Routine:
        """An anonymous code body's routine, memoised by its one text and the cell."""
        text = inline(value.statements)
        key = ("asm", text, cell)
        if key not in self.memo:
            self.machine = self.machine or self._machine()
            self.memo[key] = self.machine.compile(text, value.statements, {}, cell)
        return self.memo[key]

    def defaults(self) -> dict:
        """``{cell: ((slot, fn, once), ...)}`` of the slots' default bindings."""
        return {
            cell: tuple(
                (k, self.expression(s.mod, cell), False) for k, s in enumerate(slots) if s.mod
            )
            for cell, slots in self.slotted.items()
        }

    def event(self, event) -> CEvent:
        cmds = dict(event.commands)
        ops = self.ops((k, v) for k, v in event.commands if k not in ("tempo", "vtempo", "legato"))
        note = event.note
        if note == "-":
            note = None
        elif note not in ("off", "on"):
            note = note_number(note)
        return CEvent(
            note,
            self.instrument(event.ins),
            ops,
            event.keep_pw,
            event.keep_filter,
            event.steps or 1,
            "legato" in cmds,
            _tempo(cmds["tempo"], "tempo") if "tempo" in cmds else None,
            _tempo(cmds["vtempo"], "vtempo") if "vtempo" in cmds else None,
        )

    def phrase(self, name: str) -> tuple:
        given = not isinstance(name, str)
        key = ("phrase", id(name) if given else name)
        if key not in self.memo:
            events = name if given else self.named("phrase", self.phrases, name)
            self.memo[key] = tuple(self.event(e) for e in events)
        return self.memo[key]

    def entry(self, entry) -> tuple:
        if entry == CONT:
            return ("cont",)
        if entry == END:
            return ("end",)
        return ("phrase", self.phrase(entry.phrase), entry.transpose or 0)


def compile_document(doc: Document, compiler=None) -> Song:
    """The Song of a Document read by :func:`unitracker_player.document.read`: every name
    resolved and every expression compiled once, by ``compiler``, a ``_Compiler`` subclass when
    given."""
    head = doc.header
    voices = 3 if head.voices is None else head.voices
    if not 1 <= voices <= MAX_VOICES:
        raise PlayError(f"voices: {voices}: the player plays 1 to {MAX_VOICES} voices")
    clock_hz = head.clock[1] if head.clock else PAL_HZ
    comp = (compiler or _Compiler)(doc, note_table(clock_hz))
    for name in comp.programs:
        comp.program(name)
    for name in comp.instruments:
        comp.instrument(name)
    init = tuple(comp.event(e) for e in doc.init or ())
    if doc.init is not None and len(init) != voices:
        raise PlayError(f"init: {len(init)} events for {voices} voices")
    timeline, orders = _rows(doc, comp, voices)
    lookahead, marks, earliest = _lookahead(comp)
    frame, full = head.calls or (1, None)
    return Song(
        voices,
        _tempo(6 if head.tempo is None else head.tempo, "tempo"),
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
        {cell: sum(size for _, size in names) for cell, names in head.state or ()},
        comp.machine is not None,
        _regs(head.regs),
        head.lag or 0,
        head.global_cells or (),
        comp.kinds,
        orders,
        frame,
        full,
    )


def _lookahead(comp: _Compiler) -> tuple:
    """``(lookahead, marks, earliest)``: the most ticks a restart's rows precede a note, the
    events slots hold, reset and pause on, and the most calls any of them precedes its event."""
    instruments = [v for k, v in comp.memo.items() if k[0] == "ins"]
    marks = _marks(comp.slotted, comp.pauses())
    earliest = max((k for events in marks.values() for _, k in events), default=0)
    return max((r[0][1] for i in instruments if (r := i.restart)), default=0), marks, earliest


def _rows(doc: Document, comp: _Compiler, voices: int) -> tuple:
    """``(timeline, orders)``: the compiled rows of a song of rows, or its voices' orderlists."""
    if doc.voiced:
        return [], _orders(doc, comp, voices)
    timeline = []
    for i, row in enumerate(doc.rows):
        if len(row.play) != voices:
            raise PlayError(f"row {i}: {len(row.play)} play entries for {voices} voices")
        timeline.append(tuple(comp.entry(p) for p in row.play))
    if doc.loop is not None and doc.loop >= len(timeline):
        raise PlayError(f"song: loop row {doc.loop} out of range")
    return timeline, None


def _orders(doc: Document, comp: _Compiler, voices: int) -> tuple:
    """Per voice its compiled orderlist ``(entries, loop)``."""
    if len(doc.rows) != voices:
        raise PlayError(f"song: {len(doc.rows)} orderlists for {voices} voices")
    for v, voice in enumerate(doc.rows):
        if voice.loop is not None and voice.loop >= len(voice.play):
            raise PlayError(f"voice {v + 1}: loop entry {voice.loop} out of range")
    return tuple((tuple(map(comp.entry, voice.play)), voice.loop) for voice in doc.rows)


def _regs(regs: tuple | None) -> tuple:
    """The registers before tick 0, each a byte: all 0 where the document gives none."""
    regs = regs or (0,) * REGISTERS
    if len(regs) != REGISTERS:
        raise PlayError(f"regs: {len(regs)} values for {REGISTERS} registers")
    return tuple(r & 0xFF for r in regs)


def _marks(slots: dict, pauses: dict) -> dict:
    """``{mark: events}``: ``hold slot.cell`` and ``reset slot.cell`` for each slot, and
    ``pause slot`` for each program slot of the ``holds`` line."""
    marks = {}
    for cell, cells in slots.items():
        for slot in cells:
            marks[f"hold {slot.name}.{cell}"] = _events(slot.hold)
            marks[f"reset {slot.name}.{cell}"] = _events(slot.reset)
    marks |= {f"pause {slot}": events for slot, events in pauses.items()}
    for mark, events in marks.items():
        for event, _ in events:
            if event.startswith("row.") and event[4:] not in ROW_KEYS:
                raise PlayError(f"{mark}: {event}: no row key {event[4:]}")
    return {mark: frozenset(events) for mark, events in marks.items() if events}


def load(text: str) -> Song:
    """The Song of canonical document ``text``."""
    return compile_document(read(text))
