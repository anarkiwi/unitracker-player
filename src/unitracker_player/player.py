"""The tick machine: a canonical document played to the 25 SID registers' state per tick."""

import ast
import itertools
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from tqdm import tqdm
from unitracker_grammar import PAL_HZ

from unitracker_player.document import Document, Expr, Program, note_number, read
from unitracker_player.expr import compile_expr, constant, note_table, offset
from unitracker_player.modulation import SHAPES

VERSION = "1.20"
MAX_VOICES = 3
REGISTERS = 25
ENVELOPE = ("ad", "sr")
CONTROL, PULSE, FILTER = range(3)
FREQ_MASK, PW_MASK, CUT_MASK = 0xFFFF, 0xFFF, 0x7FF


class PlayError(ValueError):
    """A document the player refuses, or a timeline that contradicts its phrases."""


@dataclass(eq=False)
class CProgram:
    """A compiled program: rows of ``(wait, hold, ops)`` and the loop row, or None for end;
    filled after it is memoised, so a program may run itself."""

    rows: tuple = ()
    loop: int | None = None


@dataclass(frozen=True)
class CInstrument:
    """A compiled instrument; ``fn`` is its modulator's function ``f(bag, v, t, note)``, ``p0``
    the bag it starts from, and ``restart`` is its rows with the ticks each precedes the note."""

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
    """A document compiled for playing."""

    voices: int
    tempo: tuple
    clock_hz: int
    init: tuple
    timeline: tuple
    loop: int | None
    lookahead: int
    table: object
    hold: dict
    horizon: int


def _tempo(value, where: str) -> tuple:
    pair = value if isinstance(value, tuple) else (value, value)
    if min(pair) < 1:
        raise PlayError(f"{where}: a tempo of 0 ticks per step")
    return pair


def _slot(name: str) -> int:
    return PULSE if name.startswith("pw") else CONTROL if name.startswith("c") else FILTER


class _Compiler:
    """Resolves a Document's names and compiles its programs, instruments and expressions, each
    once; each method compiles the construct of its name."""

    # pylint: disable=missing-function-docstring

    def __init__(self, doc: Document, table):
        self.doc, self.table = doc, table
        self.memo: dict = {}

    def fn(self, expr: Expr, freq: bool = False):
        node = expr.node
        if freq and constant(node) is not None:
            node = offset(constant(node))
        key = ("fn", ast.dump(node))
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

    def program(self, ref) -> CProgram | None:
        if ref is None:
            return None
        key = ("program", ref if isinstance(ref, str) else id(ref))
        if key not in self.memo:
            source = self.named("program", self.doc.programs, ref) if isinstance(ref, str) else ref
            out = self.memo[key] = CProgram()
            self._fill(out, source, ref)
        return self.memo[key]

    def _fill(self, out: CProgram, source: Program, ref):
        if source.loop is not None and source.loop >= len(source.rows):
            raise PlayError(f"program {ref}: loop row {source.loop} out of range")
        out.rows = tuple((r.wait, r.hold, self.ops(r.keys)) for r in source.rows)
        out.loop = source.loop

    def ops(self, keys) -> tuple:
        out = []
        for key, value in keys:
            if key in ("pw", "cut", "freq") and value is not None:
                value = self.fn(value, freq=key == "freq")
            elif key == "run":
                value = (_slot(value), self.program(value))
            out.append((key, value))
        return tuple(out)

    def restart(self, ref) -> tuple | None:
        prog = self.program(ref)
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
        key = ("ins", name)
        if key not in self.memo:
            ins = self.named("instrument", self.doc.instruments, name)
            self.memo[key] = CInstrument(
                ins.ad,
                ins.sr,
                self.program(ins.ctrl),
                self.program(ins.pw),
                self.program(ins.filter),
                None if ins.freq is None else self.fn(ins.freq, freq=True),
                self.shape(ins.fn),
                dict(ins.p0) if ins.p0 else None,
                self.restart(ins.restart),
            )
        return self.memo[key]

    def expression(self, value, freq: bool = False):
        if value is None:
            return None
        if isinstance(value, int):
            return self.fn(Expr(ast.Constant(value)), freq)
        return self.fn(self.named("expression", self.doc.expressions, value), freq)

    def event(self, event) -> CEvent:
        cmds = dict(event.commands)
        ops = []
        for key, value in event.commands:
            if key == "run":
                value = None if value is None else (_slot(value), self.program(value))
            elif key in ("pw", "cut", "freq"):
                value = self.expression(value, key == "freq")
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
        key = ("phrase", name)
        if key not in self.memo:
            self.memo[key] = tuple(
                self.event(e) for e in self.named("phrase", self.doc.phrases, name)
            )
        return self.memo[key]

    def entry(self, entry) -> tuple:
        if entry.kind == "phrase":
            return ("phrase", self.phrase(entry.phrase), entry.transpose)
        return (entry.kind,)


def compile_document(doc: Document, compiler=None) -> Song:
    """The Song of a Document: every name resolved and every expression compiled once, by
    ``compiler``, a ``_Compiler`` subclass when given."""
    head = doc.header
    if head.version != VERSION:
        raise PlayError(f"version {head.version}: the player reads {VERSION}")
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
    hold = {cell: frozenset(events) for cell, events in head.hold.items()}
    earliest = max((k for events in hold.values() for _, k in events), default=0)
    return Song(
        head.voices,
        _tempo(head.tempo, "tempo"),
        clock_hz,
        init,
        tuple(timeline),
        doc.loop,
        lookahead,
        comp.table,
        hold,
        lookahead + earliest,
    )


def load(text: str) -> Song:
    """The Song of canonical document ``text``."""
    return compile_document(read(text))


class _Sequencer:
    """The timeline walked to the tick each event's step begins, independent of the
    registers."""

    def __init__(self, song: Song):
        n = self.voices = song.voices
        self.song = song
        self.tempo, self.phase = [song.tempo] * n, [0] * n
        self.phrase, self.pos, self.transpose = [()] * n, [0] * n, [0] * n
        self.rest, self.next, self.stopped = [0] * n, [0] * n, [False] * n
        self.row, self.end = 0, None
        for v, event in enumerate(song.init):
            self._retime(v, event)

    def _retime(self, v: int, event: CEvent):
        if event.tempo:
            self.tempo, self.phase = [event.tempo] * self.voices, [0] * self.voices
        if event.vtempo:
            self.tempo[v], self.phase[v] = event.vtempo, 0

    def _start_row(self, t: int, ending: set):
        timeline = self.song.timeline
        if self.row == len(timeline):
            if self.song.loop is None:
                self.end = t
                return
            self.row = self.song.loop
        index, self.row = self.row, self.row + 1
        for v, entry in enumerate(timeline[index]):
            if (entry[0] == "cont") == (v in ending):
                state = "ends its phrase" if v in ending else "is not at a phrase end"
                raise PlayError(f"row {index}: voice {v + 1} {state} at tick {t}")
            if entry[0] == "end":
                self.stopped[v] = True
            elif entry[0] == "phrase":
                self.phrase[v], self.pos[v], self.transpose[v] = entry[1], 0, entry[2]
        if all(self.stopped):
            self.end = t

    def advance(self, limit: int) -> list:
        """The steps that begin by tick ``limit``, as ``(tick, voice, event, transpose, last)``:
        ``event`` None for a step an event's ``*k`` times, ``last`` for a phrase's last step."""
        out = []
        while self.end is None:
            live = [v for v in range(self.voices) if not self.stopped[v]]
            t = min(self.next[v] for v in live)
            if t > limit:
                break
            due = [v for v in live if self.next[v] == t]
            ending = {v for v in due if not self.rest[v] and self.pos[v] == len(self.phrase[v])}
            if ending:
                self._start_row(t, ending)
                if self.end is not None:
                    break
                due = [v for v in due if not self.stopped[v]]
            for v in due:
                event = None
                if self.rest[v]:
                    self.rest[v] -= 1
                else:
                    event = self.phrase[v][self.pos[v]]
                    self.pos[v] += 1
                    self.rest[v] = event.steps - 1
                    self._retime(v, event)
                last = not self.rest[v] and self.pos[v] == len(self.phrase[v])
                out.append((t, v, event, self.transpose[v], last))
            for v in due:
                self.next[v] = t + self.tempo[v][self.phase[v]]
                self.phase[v] ^= 1
        return out


class _Voice:
    """A voice's cells, note state, modulators and program slots."""

    def __init__(self):
        self.freq = self.pw = self.ctrl = self.mask = self.gate = self.ad = self.sr = 0
        self.note = self.absolute = None
        self.relative = 0
        self.freq_prog = self.freq_cmd = self.pw_prog = self.pw_cmd = None
        self.osc = None
        self.slots = [[None, 0, 0] for _ in range(3)]

    @property
    def sounding(self) -> int | None:
        """N: the row's absolute note, else the event note plus the row's relative note."""
        if self.absolute is not None:
            return self.absolute
        return None if self.note is None else self.note + self.relative


class Player:  # pylint: disable=too-many-instance-attributes
    """Plays a Song one tick at a time; ``step()`` returns the registers at the end of the
    tick, or None once the song has ended. ``regs`` is the register file the last tick wrote,
    each register by its own statement, so a line tracer sees one store per register."""

    def __init__(self, song: Song):
        self.song = song
        self.tick = 0
        self.regs = [0] * REGISTERS
        self.voices = [_Voice() for _ in range(song.voices)]
        self.cut = self.res = self.route = self.mode = self.vol = 0
        self.cut_prog = self.cut_cmd = None
        self._seq = _Sequencer(song)
        self._ins = [None] * song.voices
        self._events = defaultdict(list)
        self._lookahead = defaultdict(list)
        self._held = defaultdict(set)
        self._noted = [None] * song.voices
        self._init = [i for v, e in enumerate(song.init) for _, i in self._schedule(0, v, e, 0)]

    @property
    def finished(self) -> bool:
        """Whether the song has ended at the current tick."""
        return self._seq.end is not None and self.tick >= self._seq.end

    def _schedule(self, t: int, v: int, event: CEvent, transpose: int) -> list:
        """The ``(tick, event item)`` pairs of an event: a note-less one split at its
        restart's lookahead, its gate (part ``gate``) on the first row's tick and its commands
        (part ``ops``) on the last's."""
        if event.ins is not None:
            self._ins[v] = event.ins
        ins = self._ins[v]
        if ins is None and any(k == "run" and val is not None for k, val in event.ops):
            raise PlayError(f"voice {v + 1}: !run at tick {t} on a voice with no instrument")
        if not ins or not ins.restart or event.legato:
            return [(t, (v, event, transpose, ins, None, None))]
        *ahead, (last, _) = ins.restart
        if isinstance(event.note, int):
            if ahead:
                wait, hold, ops = ahead[-1][0]
                envelope = tuple(op for op in event.ops if op[0] in ENVELOPE)
                ahead[-1] = ((wait, hold, ops + envelope), ahead[-1][1])
            for row, before in ahead:
                if t - before >= 0:
                    self._lookahead[t - before].append((v, row))
            return [(t, (v, event, transpose, ins, last, None))]
        if not ahead:
            return [(t, (v, event, transpose, ins, None, None))]
        parts = [(ahead[0][1], "gate", event.note), (ahead[-1][1], "ops", event.ops)]
        return [
            (max(t - before, 0), (v, event, transpose, ins, None, part))
            for before, part, has in parts
            if has
        ]

    def _hold_step(self, t: int, v: int, event: CEvent | None, last: bool):
        """The hold ticks of voice ``v``'s step at tick ``t``: the tick of each event it is, and
        of its read, by the restart of the voice's instrument before the step's event."""
        at = {"step": t, "last": t if last else None, "read": None}
        if event is not None and isinstance(event.note, int):
            at["legato" if event.legato else "note"] = t
        ins = self._ins[v]
        if ins is not None:
            at["read"] = t - (ins.restart[0][1] if ins.restart else 0)
        for cell, events in self.song.hold.items():
            self._held[v, cell].update(at[e] - k for e, k in events if at.get(e) is not None)

    def _holds(self, v: int, cell: str, t: int) -> bool:
        """Whether tick ``t`` is one of voice ``v``'s hold events of ``cell``."""
        rowed = ("row", 0) in self.song.hold.get(cell, ()) and t in self._held[v, "row"]
        return rowed or t in self._held[v, cell]

    def _advance(self, t: int):
        """Schedules the steps that begin by the song's horizon past tick ``t``."""
        for tick, v, event, transpose, last in self._seq.advance(t + self.song.horizon):
            self._hold_step(tick, v, event, last)
            if event is not None:
                for at, item in self._schedule(tick, v, event, transpose):
                    self._events[at].append(item)

    def step(self) -> list | None:
        """Plays one tick."""
        t = self.tick
        self._advance(t)
        if self.finished:
            return None
        if t == 0:
            for item in self._init:
                self._event(t, *item)
        rows, events = self._lookahead.pop(t, ()), self._events.pop(t, ())
        for v in range(len(self.voices)):
            for rv, row in rows:
                if rv == v:
                    self._row(v, row, t)
            for item in events:
                if item[0] == v:
                    self._event(t, *item)
        for kind in (CONTROL, PULSE, FILTER):
            for v in range(len(self.voices)):
                self._run_slot(v, kind, t)
        self._fire()
        self.tick += 1
        return self.registers()

    def registers(self) -> list:
        """The 25 register values, written to ``regs``."""
        regs = self.regs
        for v, voice in enumerate(self.voices):
            at = 7 * v
            regs[at] = voice.freq & 0xFF
            regs[at + 1] = voice.freq >> 8
            regs[at + 2] = voice.pw & 0xFF
            regs[at + 3] = voice.pw >> 8
            regs[at + 4] = ((voice.ctrl ^ voice.mask) & 0xFE) | (voice.ctrl & voice.gate & 1)
            regs[at + 5] = voice.ad
            regs[at + 6] = voice.sr
        regs[21] = self.cut & 7
        regs[22] = self.cut >> 3
        regs[23] = self.res << 4 | self.route
        regs[24] = self.mode | self.vol
        return list(regs)

    def _start(self, v: int, kind: int, prog: CProgram | None, t: int):
        running = prog is not None and bool(prog.rows)
        self.voices[v].slots[kind] = [prog, 0, t + prog.rows[0][0]] if running else [None, 0, 0]

    def _stop_pulse(self, v: int):
        voice = self.voices[v]
        voice.slots[PULSE][0] = voice.pw_prog = None

    def _stop_filter(self, v: int):
        self.voices[v].slots[FILTER][0] = None
        if self.cut_prog and self.cut_prog[2] == v:
            self.cut_prog = None

    def _run_slot(self, v: int, kind: int, t: int):
        slots, seen = self.voices[v].slots, set()
        while (slot := slots[kind])[0] is not None and slot[2] <= t:
            prog, index = slot[0], slot[1]
            if (id(prog), index) in seen:
                raise PlayError(f"voice {v + 1}: a program loops in no time at tick {t}")
            seen.add((id(prog), index))
            _, hold, ops = prog.rows[index]
            self._apply(v, ops, t, False, kind == CONTROL)
            if kind == CONTROL and any(key not in ("ctrl", "gate") for key, _ in ops):
                self._held[v, "row"].add(t)
            if slots[kind] is not slot or slot[0] is None:
                continue
            index += 1
            if index == len(prog.rows):
                if prog.loop is None:
                    slot[0] = None
                    break
                index = prog.loop
            slot[1], slot[2] = index, t + hold + prog.rows[index][0]

    def _row(self, v: int, row: tuple, t: int, first: bool = False):
        self._apply(v, row[2], t, first)

    # pylint: disable-next=too-many-branches,too-many-arguments,too-many-positional-arguments
    def _apply(self, v: int, ops: tuple, t: int, first: bool, ctrl: bool = False):
        """A row's keys, in vocabulary order; ``first`` marks a restart's first-tick row,
        ``ctrl`` a control program's row, whose modulators hold on the ``ctrl.`` cells."""
        held = "ctrl." if ctrl else ""
        voice = self.voices[v]
        for key, value in ops:
            if key == "ctrl":
                ctrl, mask = value
                if ctrl is not None:
                    voice.ctrl = ctrl & 0xFF
                if mask is not None:
                    voice.mask = mask & 0xFE
                if first:
                    voice.gate = 1
            elif key == "gate":
                voice.gate = 1 if value else 0
            elif key == "note":
                if value[0] == "rel":
                    voice.relative, voice.absolute = value[1], None
                else:
                    voice.absolute = value[1]
            elif key in ("ad", "sr"):
                setattr(voice, key, value & 0xFF)
            elif key == "pw":
                if value is None:
                    self._stop_pulse(v)
                else:
                    voice.pw_prog = [value, 0, held + "pw", None, None]
            elif key == "cut":
                self.cut_prog = [value, 0, v, held + "cut", None, None]
            elif key == "freq":
                voice.freq_prog = [value, 0, held + "freq", self._before(v, t), None]
            elif key == "run":
                self._start(v, value[0], value[1], t)
            elif key == "filter":
                self._stop_filter(v)
            else:
                self._global(v, key, value)

    def _global(self, v: int, key: str, value):
        if key == "res":
            self.res = value & 15
        elif key == "route":
            form, mask = value
            bits = (1 << v | mask) & 15 if form != "mask" else mask & 15
            self.route = (
                bits
                if form == "mask"
                else self.route | bits if form == "add" else self.route & ~bits
            )
        elif key == "mode":
            self.mode = value & 0xF0
        elif key == "vol":
            self.vol = value & 15

    def _event(
        self, t: int, v: int, event: CEvent, transpose: int, ins, last, part
    ):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Event ``event``, or its ``part``: ``gate`` its gate alone, ``ops`` its commands."""
        voice = self.voices[v]
        if last is not None:
            self._row(v, last, t, True)
        if isinstance(event.note, int):
            voice.note = event.note + transpose
            if not event.legato:
                voice.relative, voice.absolute, voice.gate = 0, None, 1
                self._noted[v] = t
                if ins is not None:
                    self._instrument(v, ins, event, t)
        elif event.note is not None and part != "ops":
            voice.gate = 1 if event.note == "on" else 0
        for key, value in event.ops if part != "gate" else ():
            self._command(v, key, value, t)

    def _instrument(self, v: int, ins: CInstrument, event: CEvent, t: int):
        voice = self.voices[v]
        if ins.ad is not None:
            voice.ad = ins.ad & 0xFF
        if ins.sr is not None:
            voice.sr = ins.sr & 0xFF
        if ins.ctrl is not None:
            self._start(v, CONTROL, ins.ctrl, t)
        if ins.pw is not None and not event.keep_pw:
            self._start(v, PULSE, ins.pw, t)
        if ins.filter is not None and not event.keep_filter:
            self._start(v, FILTER, ins.filter, t)
        voice.freq_prog = None if ins.freq is None else [ins.freq, 0, "freq", -1, None]
        self._arm_osc(voice, ins.fn, ins.p0)
        voice.freq_cmd = voice.pw_cmd = None
        if self.cut_cmd and self.cut_cmd[2] == v:
            self.cut_cmd = None

    def _command(self, v: int, key: str, value, t: int):  # pylint: disable=too-many-branches
        voice = self.voices[v]
        if key == "run":
            if value is None:
                voice.slots[CONTROL][0] = None
            else:
                self._start(v, value[0], value[1], t)
        elif key == "pw":
            voice.pw_cmd = None if value is None else [value, 0, "pw", None, None]
            if value is None:
                self._stop_pulse(v)
        elif key == "filter":
            self._stop_filter(v)
            if self.cut_cmd and self.cut_cmd[2] == v:
                self.cut_cmd = None
        elif key in ("ad", "sr"):
            setattr(voice, key, value & 0xFF)
        elif key == "ctrl":
            voice.ctrl = value & 0xFF
        elif key == "resfilt":
            self.res, self.route = value[0] & 15, value[1] & 15
        elif key == "cut":
            self.cut_cmd = [value, 0, v, "cut", None, None]
        elif key == "freq":
            voice.freq_cmd = None if value is None else [value, 0, "freq", self._before(v, t), None]
            if value is not None:
                voice.osc = None
        elif key == "set":
            if voice.osc is None:
                raise PlayError(f"voice {v + 1}: !set at tick {t} with no frequency modulator")
            voice.osc[1].update(dict(value) if value is not None else {})
        else:
            self._global(v, key, value)

    def _arm_osc(self, voice, fn, p0):
        if fn is None:
            voice.osc = None
            return
        voice.osc = [fn, dict(p0) if p0 else {}, 0, "freq", -1, None]

    def _owner(self, t: int, cmd: list | None, prog: list | None, v: int | None = None) -> tuple:
        """``(modulator, held)`` of the command, else the program modulator, held by voice
        ``v`` (the cutoff's: the voice that set it); ``(None, False)`` for neither."""
        mod = cmd or prog
        if mod is None:
            return None, False
        return mod, self._holds(mod[2] if v is None else v, mod[-3], t)

    def _before(self, v: int, t: int) -> int | None:
        """A frequency modulator's φ before its first firing: -1 for one set on the tick a note
        starts, else None (the frequency keeps its value)."""
        return -1 if self._noted[v] == t else None

    def _fire(self):
        t = self.tick
        for vix, voice in enumerate(self.voices):
            n = voice.sounding
            if voice.osc is not None:
                base = self.song.table(n) if n is not None else 0
                fn, bag = voice.osc[0], voice.osc[1]
                base = (base + bag.get("detune", 0) * vix) & FREQ_MASK
                if at := _firing(voice.osc, 2, self._holds(vix, "freq", t), voice.freq):
                    voice.freq = fn(bag, *at, base) & FREQ_MASK
                continue
            mod, held = self._owner(t, voice.freq_cmd, voice.freq_prog, vix)
            if mod is None:
                if n is not None:
                    voice.freq = self.song.table(n)
            elif at := _firing(mod, 1, held, voice.freq):
                voice.freq = mod[0](*at, n or 0) & FREQ_MASK
        for vix, voice in enumerate(self.voices):
            mod, held = self._owner(t, voice.pw_cmd, voice.pw_prog, vix)
            if mod and (at := _firing(mod, 1, held, voice.pw)):
                voice.pw = mod[0](*at, voice.sounding or 0) & PW_MASK
        mod, held = self._owner(t, self.cut_cmd, self.cut_prog)
        if mod and (at := _firing(mod, 1, held, self.cut)):
            self.cut = mod[0](*at, self.voices[mod[2]].sounding or 0) & CUT_MASK
        for ticks in self._held.values():
            ticks.discard(t)


def _firing(mod: list, at: int, held: bool, v: int) -> tuple | None:
    """The ``(v, φ)`` modulator ``mod`` (its φ at ``at``, then its key, its φ before its first
    firing and its last firing's ``(v, φ)``) is evaluated at with the cell's value ``v``, or
    None to keep it: on a hold tick its last firing's, before its first ``(v, φ before)`` or
    None, φ not advancing; else ``v`` and its φ, which then advances."""
    if held:
        return mod[-1] or (None if mod[-2] is None else (v, mod[-2]))
    mod[-1] = (v, mod[at])
    mod[at] += 1
    return mod[-1]


def play(source, ticks: int | None = None, progress: bool = False) -> np.ndarray:
    """The registers at the end of each tick of a document's text or Song, shape ``(ticks,
    25)``: to the song's end when ``ticks`` is None (a looping song needs ``ticks``), and fewer
    rows where the song ends first."""
    song = load(source) if isinstance(source, str) else source
    if ticks is None and song.loop is not None:
        raise PlayError("a looping song needs ticks")
    player = Player(song)
    frames = np.zeros((ticks or 0, REGISTERS), dtype=np.uint8)
    rows = []
    span = range(ticks) if ticks is not None else itertools.count()
    for t in tqdm(span, total=ticks, unit="tick", disable=not progress):
        regs = player.step()
        if regs is None:
            break
        if ticks is None:
            rows.append(regs)
        else:
            frames[t] = regs
    if ticks is None:
        return np.array(rows, dtype=np.uint8).reshape(-1, REGISTERS)
    return frames[: player.tick]
