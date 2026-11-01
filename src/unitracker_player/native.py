"""A compiled Song as the flat stream of integers the native call machine reads
(``unitracker_player._core``), and the machine's use by :func:`unitracker_player.player.play`."""

import ast
import importlib
import os

from unitracker_grammar.cells import CELLS, SONG_CELLS
from unitracker_grammar.model import HOLD, NONE, RESET

from unitracker_player.code import Routine
from unitracker_player.compiler import (
    CELL_OF,
    ENVELOPE,
    EVENT_KEY,
    FILTER,
    REGISTERS,
    SLOT_PHASES,
    Song,
    written,
)
from unitracker_player.nodes import children, op

try:
    _core = importlib.import_module("unitracker_player._core")
    NativeError = _core.NativeError
except ImportError:  # pragma: no cover - the pure Python player stands in
    _core = None

    class NativeError(RuntimeError):  # type: ignore[no-redef]
        """The native call machine could not play the song."""


MAGIC = 0x55545031
LIMIT = 1 << 62
F_RANGE = range(-1024, 1024)
CELL_ID = {cell: i for i, cell in enumerate(CELLS)}
ENV, MASK, GATE, NOTE_REL, NOTE_ABS, ROUTE_ON, ROUTE_OFF, RUN, BIND, RESET_OP, CLEAR_OP, HOLD_OP = (
    range(12)
)
STOP_TAG = {RESET: RESET_OP, NONE: CLEAR_OP, HOLD: HOLD_OP}
MARK_EVENTS = {"step": 0, "last": 1, "read": 2, "event": 3, "note": 4, "legato": 5}
CODES = {
    "leaf": None,
    "+": 5,
    "-": 6,
    "*": 7,
    "//": 8,
    "%": 9,
    "|": 10,
    "^": 11,
    "&": 12,
    "<<": 13,
    ">>": 14,
    "neg": 15,
    "~": 16,
    "!": 17,
    "==": 18,
    "!=": 19,
    "<": 20,
    "<=": 21,
    ">": 22,
    ">=": 23,
    "&&": 24,
    "||": 25,
    "abs": 26,
    "min": 27,
    "max": 28,
    "F": 29,
    "lo": 30,
    "hi": 31,
    "u8": 32,
    "u16": 33,
    "sx": 34,
    "ite": 35,
    "T": 36,
}
NAME_CODES = {"v": 1, "phi": 2, "N": 3, "n": 4}


class Unsupported(ValueError):
    """A document the native call machine does not play."""


def enabled() -> bool:
    """Whether the native call machine is built in and not switched off by the environment
    variable ``UNITRACKER_PLAYER_NATIVE=0``."""
    return _core is not None and os.environ.get("UNITRACKER_PLAYER_NATIVE", "1") != "0"


def postfix(node: ast.expr, tables: dict) -> list:
    """The postfix code of an expression node; ``tables`` maps a table's name to its index."""
    out = []

    def walk(n):
        name = op(n)
        if name == "leaf":
            if isinstance(n, ast.Constant):
                if not -LIMIT < n.value < LIMIT:
                    raise Unsupported("a constant past 62 bits")
                out.extend((0, int(n.value)))
            else:
                out.append(NAME_CODES[n.id])
            return
        for child in children(n):
            walk(child)
        out.append(CODES[name])
        if name in ("&&", "||"):
            out.append(len(children(n)))
        elif name == "T":
            out.append(tables[n.value.id])

    walk(node)
    return out


class _Export:  # pylint: disable=too-many-instance-attributes
    """The stream of one Song; ``fns``, ``progs``, ``inss``, ``events`` and ``phrases`` memoise
    by identity the objects the stream indexes."""

    def __init__(self, song: Song):
        self.song = song
        self.kinds = song.kinds
        self.ops, self.rows, self.row_events = [], [], []
        self.envs, self.env_ids = [], {}
        self.progs, self.prog_ids = [], {}
        self.inss, self.ins_ids = [], {}
        self.events, self.event_ids = [], {}
        self.phrases, self.phrase_ids = [], {}
        self.layers = []
        self.fns, self.fn_ids = [], {}
        self.routine_ids, self.machine = {}, None
        self.marks = sorted(song.marks)
        self.mark_ids = {m: i for i, m in enumerate(self.marks)}
        names = sorted({e for rows in song.rows.values() for e in rows})
        self.row_names = {e: i for i, e in enumerate(names)}
        self.tables = {}

    def fn(self, fn) -> int:
        """The index of modulator ``fn``."""
        if id(fn) not in self.fn_ids:
            self.fn_ids[id(fn)] = len(self.fns)
            self.fns.append(fn)
            if isinstance(fn, Routine):
                self.machine = self.machine or fn.machine
            else:
                scope = fn.__globals__["_T"]
                for name, table in scope.items():
                    self.tables.setdefault(name, table.args[0])
        return self.fn_ids[id(fn)]

    def env(self, segments: tuple) -> int:
        """The index of an env's segments."""
        key = tuple(segments)
        if key not in self.env_ids:
            self.env_ids[key] = len(self.envs)
            self.envs.append(key)
        return self.env_ids[key]

    def op(self, key: str, value) -> tuple:
        """``(tag, cell, a, b, once, written, chip)`` of a row's or event's op, a program it
        runs exported first."""
        # pylint: disable=too-many-return-statements
        cell = CELL_ID.get(key, 0)
        if key == "env":
            return ENV, 0, self.env(value), 0, 0, 0, 0
        if key == "mask":
            return MASK, 0, int(value), 0, 0, 0, 0
        if key == "gate":
            return GATE, 0, 1 if value else 0, 0, 0, 0, 0
        if key == "note":
            return (NOTE_REL if value[0] == "rel" else NOTE_ABS), 0, value[1], 0, 0, 0, 0
        if key in ("route_on", "route_off"):
            return (ROUTE_ON if key == "route_on" else ROUTE_OFF), 0, int(value), 0, 0, 0, 0
        if key == "run":
            slot, prog = value
            chip = int(self.kinds[slot] == FILTER)
            return RUN, 0, slot, self.prog(prog), 0, 0, chip
        if key not in CELL_ID:
            raise Unsupported(f"op {key}")
        chip = int(key in SONG_CELLS)
        if isinstance(value[0], int):
            slot, fn, once = value
            return BIND, cell, slot, self.fn(fn), int(once), int(written(value)), chip
        mask = sum(1 << s for s in value[1])
        return STOP_TAG[value[0]], cell, mask, 0, 0, 0, chip

    def encode(self, ops) -> list:
        """The encoded ``ops`` of ``(key, value)`` pairs."""
        return [self.op(key, value) for key, value in ops]

    def span(self, encoded: list) -> tuple:
        """``(start, length)`` of ``encoded`` ops added contiguously."""
        start = len(self.ops)
        self.ops.extend(encoded)
        return start, len(encoded)

    def row_ids(self, ops) -> list:
        """The indexes of the row events a row of ``ops`` notes."""
        keys = {EVENT_KEY.get(key, key) for key, _ in ops}
        names = {"row." + k for k in keys}
        if keys - {"ctrl", "gate"}:
            names.add("row")
        return sorted(self.row_names[n] for n in names if n in self.row_names)

    def add_rows(self, rows, befores=None) -> tuple:
        """``(start, length)`` of ``rows`` of ``(wait, hold, ops)`` added contiguously, their ops
        exported first."""
        encoded = [(w, h, self.encode(ops), self.row_ids(ops)) for w, h, ops in rows]
        start = len(self.rows)
        for k, (wait, hold, ops, ids) in enumerate(encoded):
            ev = len(self.row_events)
            self.row_events.extend(ids)
            before = befores[k] if befores else 0
            self.rows.append((wait, hold, *self.span(ops), ev, len(ids), before))
        return start, len(encoded)

    def prog(self, prog) -> int:
        """The index of program ``prog``, -1 for None."""
        if prog is None:
            return -1
        if id(prog) not in self.prog_ids:
            at = self.prog_ids[id(prog)] = len(self.progs)
            self.progs.append(None)
            span = self.add_rows(prog.rows)
            self.progs[at] = (*span, -1 if prog.loop is None else prog.loop, int(prog.fresh))
        return self.prog_ids[id(prog)]

    def ins(self, ins) -> int:
        """The index of instrument ``ins``, -1 for None."""
        if ins is None:
            return -1
        if id(ins) not in self.ins_ids:
            at = self.ins_ids[id(ins)] = len(self.inss)
            self.inss.append(None)
            progs = (self.prog(ins.ctrl), self.prog(ins.pw), self.prog(ins.filter))
            binds = self.span([self.op(cell, o) for cell, o in ins.binds])
            restart = (0, 0)
            if ins.restart:
                rows = [r for r, _ in ins.restart]
                restart = self.add_rows(rows, [t for _, t in ins.restart])
            pairs = [(slot, self.prog(p)) for slot, p in ins.layers]
            layers = (len(self.layers), len(pairs))
            self.layers.extend(pairs)
            self.inss[at] = (*progs, *binds, *restart, *layers)
        return self.ins_ids[id(ins)]

    def event(self, event) -> int:
        """The index of event ``event``."""
        if id(event) not in self.event_ids:
            at = self.event_ids[id(event)] = len(self.events)
            self.events.append(None)
            note = event.note
            kind, value = (0, 0) if note is None else (2, 0) if note == "on" else (3, 0)
            if isinstance(note, int):
                kind, value = 1, note
            ins = self.ins(event.ins)
            encoded = self.encode(event.ops)
            ops = self.span(encoded)
            kept = [(k, v) for k, v in event.ops if k in ENVELOPE and written(v)]
            envelope = self.span(self.encode(kept))
            tempo = event.tempo or (-1, -1)
            vtempo = event.vtempo or (-1, -1)
            self.events[at] = (
                kind,
                value,
                ins,
                *ops,
                int(event.keep_pw),
                int(event.keep_filter),
                event.steps,
                int(event.legato),
                *tempo,
                *vtempo,
                *envelope,
            )
        return self.event_ids[id(event)]

    def phrase(self, events: tuple) -> int:
        """The index of the phrase of ``events``."""
        if id(events) not in self.phrase_ids:
            self.phrase_ids[id(events)] = len(self.phrases)
            self.phrases.append([self.event(e) for e in events])
        return self.phrase_ids[id(events)]

    def entries(self, entries) -> list:
        """``[n, (kind, phrase, transpose)...]`` of timeline or orderlist ``entries``."""
        out = [len(entries)]
        for entry in entries:
            if entry[0] == "phrase":
                out += [2, self.phrase(entry[1]), entry[2]]
            else:
                out += [0 if entry[0] == "cont" else 1, 0, 0]
        return out

    def structure(self) -> list:
        """The timeline's or orderlists' entries, phrases exported."""
        song = self.song
        if song.orders is None:
            out = [0, len(song.timeline)]
            for row in song.timeline:
                out += self.entries(row)
            return out + [-1 if song.loop is None else song.loop]
        out = [1]
        for entries, loop in song.orders:
            out += self.entries(entries) + [-1 if loop is None else loop]
        return out

    def functions(self) -> list:
        """The modulators: a postfix code each, or the index of the routine."""
        names = {n: i for i, n in enumerate(self.tables)}
        out = [len(self.fns)]
        for fn in self.fns:
            if isinstance(fn, Routine):
                out += [1, self.routine_ids[id(fn)]]
            else:
                code = postfix(fn.node, names)
                out += [0, len(code), *code]
        return out

    def routines_stream(self) -> list:
        """The routines in the order the modulators named them."""
        routines = [fn for fn in self.fns if isinstance(fn, Routine)]
        out = [len(routines)]
        for fn in sorted(routines, key=lambda f: self.routine_ids[id(f)]):
            out += [fn.start, fn.end, len(fn.dirty), *(x for span in fn.dirty for x in span)]
        return out

    def head(self) -> list:
        """The song's scalars, phases, kinds and layers, slots, shared and state."""
        song = self.song
        out = [MAGIC, song.voices, song.frame, song.lag]
        out += [len(song.full or ()), *map(int, song.full or ())]
        out += list(song.regs)
        out += [song.horizon, *song.tempo]
        phases = []
        for name in song.order:
            if cell := CELL_OF.get(name):
                phases.append((0, CELL_ID[cell]))
            elif name in SLOT_PHASES:
                phases.append((1, SLOT_PHASES[name]))
            else:
                phases.append((2, int(name == "read")))
        out += [len(phases), *(x for p in phases for x in p), int("read" in song.order)]
        out += [len(self.kinds), *self.kinds]
        for kind in range(3):
            layer = [s for s, own in enumerate(self.kinds) if own == kind]
            out += [len(layer), *layer]
        out += [len(song.slots[cell]) for cell in CELLS]
        out += [int(cell in song.shared) for cell in CELLS]
        out += [song.state.get(cell, 0) for cell in CELLS]
        return out

    def tables_stream(self) -> list:
        """The note table and the named tables."""
        values = [self.song.table(k) for k in F_RANGE]
        out = [F_RANGE.start, len(values), *values, len(self.tables)]
        for table in self.tables.values():
            out += [len(table), *table]
        return out

    def marks_stream(self) -> list:
        """The marks with their events and row events, the slots' reset and hold marks and the
        program slots' pauses."""
        song = self.song
        out = [len(self.marks)]
        for mark in self.marks:
            events = [(MARK_EVENTS.get(e, 99), k) for e, k in song.marks[mark] if e[:3] != "row"]
            out += [len(events), *(x for ev in events for x in ev)]
            rows = [self.row_names[e] for e in song.rows.get(mark, ())]
            out += [len(rows), *rows]
        out += [len(self.row_names)]
        for cell in CELLS:
            for name in song.slots[cell]:
                out += [
                    self.mark_ids.get(f"reset {name}.{cell}", -1),
                    self.mark_ids.get(f"hold {name}.{cell}", -1),
                ]
        pauses = {int(m.split()[1]): m for m in song.marks if m.startswith("pause")}
        return out + [self.mark_ids.get(pauses.get(k), -1) for k in range(len(self.kinds))]

    def arenas(self) -> list:
        """The envs, ops, rows, programs, layers, instruments, events and phrases."""
        out = [len(self.envs)]
        for segs in self.envs:
            out += [len(segs), *(-1 if x is None else x for seg in segs for x in seg)]
        out += [len(self.ops), *(x for o in self.ops for x in o)]
        out += [len(self.row_events), *self.row_events]
        for arena in (self.rows, self.progs, self.layers, self.inss, self.events):
            out += [len(arena), *(x for record in arena for x in record)]
        out += [len(self.phrases)]
        for events in self.phrases:
            out += [len(events), *events]
        return out

    def stream(self) -> list:
        """The whole stream."""
        song = self.song
        structure = self.structure()
        init = [self.event(e) for e in song.init]
        defaults = [
            (CELL_ID[cell], k, self.fn(fn), int(once))
            for cell, bound in song.defaults.items()
            for k, fn, once in bound
        ]
        for fn in self.fns:
            if isinstance(fn, Routine):
                self.routine_ids.setdefault(id(fn), len(self.routine_ids))
        out = self.head() + self.tables_stream() + self.marks_stream()
        out += self.functions() + self.routines_stream() + self.arenas() + structure
        out += [len(init), *init, len(defaults), *(x for d in defaults for x in d)]
        return out


def export(song: Song) -> tuple:
    """``(stream, image)``: the flat stream of ``song`` and the 6502 memory of its code
    modulators (empty for none); Unsupported where a construct has no native form."""
    writer = _Export(song)
    try:
        stream = writer.stream()
    except (KeyError, AttributeError, TypeError) as exc:
        raise Unsupported(f"{type(exc).__name__}: {exc}") from exc
    image = bytes(writer.machine.image) if writer.machine is not None else b""
    return stream, image


def player(song: Song):
    """A native player of ``song``, None where there is no native core or the song has no
    native form; the song's stream is exported once."""
    if not enabled():
        return None
    cached = song.__dict__.get("_native")
    if cached is None:
        try:
            cached = export(song)
        except Unsupported:
            cached = ()
        object.__setattr__(song, "_native", cached)
    if not cached:
        return None
    try:
        return _core.NativePlayer(*cached)
    except _core.NativeError:
        return None


def run(native, ticks: int | None, progress: bool = False, chunk: int = 4096):
    """The register rows of the native player over ``ticks`` frames, to the song's end where
    None; a ``bytes`` of ``25 * frames``."""
    # pylint: disable=import-outside-toplevel
    from tqdm import tqdm

    parts, left = [], ticks
    with tqdm(total=ticks, unit="tick", disable=not progress) as counter:
        while left is None or left > 0:
            want = chunk if left is None else min(chunk, left)
            rows = native.run(want)
            parts.append(rows)
            counter.update(len(rows) // REGISTERS)
            if len(rows) < want * REGISTERS:
                break
            if left is not None:
                left -= want
    return b"".join(parts)
