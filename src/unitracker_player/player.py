"""The call machine: a canonical document played to the 25 SID registers' state per frame."""

import itertools
from collections import defaultdict, deque

import numpy as np
from tqdm import tqdm
from unitracker_grammar.cells import MASKS, SONG_CELLS, VOICE_CELLS

from unitracker_player.code import Routine
from unitracker_player.compiler import (
    CELL_OF,
    CONTROL,
    ENVELOPE,
    EVENT_KEY,
    FILTER,
    HOLD,
    PULSE,
    REGISTERS,
    RESET,
    SLOT_PHASES,
    SLOTS,
    CEvent,
    CInstrument,
    CProgram,
    Song,
    compile_document,
    load,
    written,
)
from unitracker_player.sequencer import PlayError, Sequencer

OBSERVED = {
    "event": "(voice, event, instrument): an event begins to apply",
    "enter": "(scope, voice, *args): ``apply`` with ``(first, command)`` for the keys of a row or "
    "an event's command, ``instrument`` with ``(instrument)`` for a note's instrument; the binds, "
    "stops and starts until its ``leave`` are theirs",
    "leave": "(scope): the scope entered ends",
    "row": "(voice, slot): a program slot's row is about to apply",
    "start": "(voice, slot, program): a program is about to start in a program slot, None to stop",
    "rewind": "(voice, name): a slot's phi is back at 0 on its reset event, ``name`` its "
    "``slot.cell``",
    "bind": "(voice, cell, slot, command, fn, once, fresh): a binding is about to be set, by a "
    "command or a row",
    "stop": "(voice, cell, value, ends): a ``none``, ``hold`` or ``reset`` is about to apply",
    "fire": "(cell, owner, slot, by, fn, phi, value, out, N, n): a slot fired; ``owner`` is the "
    "voice of the cell (0 for a song cell), ``by`` the voice that bound the slot",
    "cell": "(cell, voice): a voice's cell has settled in its phase",
}
__all__ = [
    "OBSERVED",
    "REGISTERS",
    "Player",
    "PlayError",
    "Song",
    "compile_document",
    "load",
    "play",
]


class _Voice:  # pylint: disable=too-many-instance-attributes
    """A voice's cells, note state, modulators and program slots; ``mods`` holds its cells'
    modulator slots, each modulator ``[fn, phi, voice, slot.cell, by command, once]``, and
    ``lost`` the calls each program slot has not counted."""

    def __init__(self, song: Song):
        self.freq = self.pw = self.ctrl = self.ad = self.sr = self.mask = self.gate = 0
        self.note = self.absolute = None
        self.relative = 0
        self.mods = {cell: [None] * len(song.slots[cell]) for cell in VOICE_CELLS}
        self.freq_hold = False
        self.slots = [[None, 0, 0] for _ in song.kinds]
        self.lost = [0] * len(song.kinds)
        self.state = {cell: bytearray(song.state.get(cell, 0)) for cell in VOICE_CELLS}

    @property
    def sounding(self) -> int | None:
        """N: the row's absolute note, else the event note plus the row's relative note."""
        if self.absolute is not None:
            return self.absolute
        return None if self.note is None else self.note + self.relative


class Player:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """Plays a Song one frame at a time; ``step()`` returns the registers after the frame's last
    call, those computed ``lag`` calls before, or None once the song has ended. ``regs`` is the
    register file the last call wrote, each register by its own statement, so a line tracer sees
    one store per register. ``observe(kind, *args)``, where given, is told of the machine's
    steps, as the :data:`OBSERVED` list says."""

    def __init__(self, song: Song, observe=None):
        self.song, self.observe = song, observe
        self.call = self.tick = self._stick = 0
        self.regs = [0] * REGISTERS
        self.voices = [_Voice(song) for _ in range(song.voices)]
        self._layers = {k: [s for s, own in enumerate(song.kinds) if own == k] for k in SLOTS}
        self.chip = dict.fromkeys(SONG_CELLS, 0)
        self.song_mods = {cell: [None] * len(song.slots[cell]) for cell in SONG_CELLS}
        self.song_state = {cell: bytearray(song.state.get(cell, 0)) for cell in SONG_CELLS}
        self.owner = dict.fromkeys(SONG_CELLS)
        self._preset(song.regs)
        self._delay = deque(list(song.regs) for _ in range(song.lag))
        self._shared = frozenset(song.shared)
        self._seq = Sequencer(song)
        self._ins = [None] * song.voices
        self._events = defaultdict(list)
        self._lookahead = defaultdict(list)
        self._held = defaultdict(set)
        self._skipped = []
        self._pauses = {int(m.split()[1]): m for m in song.marks if m.startswith("pause")}
        self._phases = [(name, CELL_OF.get(name)) for name in song.order]
        self._first = song.call_of(0)
        self._init = [i for v, e in enumerate(song.init) for _, i in self._schedule(0, v, e, 0)]
        for cell, bound in song.defaults.items():
            for v in range(song.voices if cell in VOICE_CELLS else 1):
                for binding in bound:
                    self._set(v, cell, *binding, False)

    def _preset(self, regs: tuple):
        """The cells whose registers are ``regs``, the register file ``regs``."""
        self.regs = list(regs)
        for v, voice in enumerate(self.voices):
            at = 7 * v
            voice.freq = regs[at] | regs[at + 1] << 8
            voice.pw = regs[at + 2] | (regs[at + 3] & 15) << 8
            voice.ctrl, voice.gate = regs[at + 4], regs[at + 4] & 1
            voice.ad, voice.sr = regs[at + 5], regs[at + 6]
        self.chip |= {
            "cut": regs[21] & 7 | regs[22] << 3,
            "res": regs[23] >> 4,
            "route": regs[23] & 15,
            "mode": regs[24] & 0xF0,
            "vol": regs[24] & 15,
        }

    @property
    def passes(self) -> list:
        """The sequencer ticks at which the song passed its loop."""
        return self._seq.passes

    @property
    def ticks_played(self) -> int:
        """The sequencer ticks played: the full calls run."""
        return self._stick

    @property
    def finished(self) -> bool:
        """Whether the song has ended at the current call."""
        end = self._seq.end
        return end is not None and self.call >= self.song.call_of(end)

    def _schedule(self, s: int, v: int, event: CEvent, transpose: int) -> list:
        """The ``(tick, event item)`` pairs of an event at sequencer tick ``s``: a note-less one
        split at its restart's lookahead, its gate (part ``gate``) on the first row's tick, its
        voice's commands (part ``ops``) on the last's and its song cells' ones (part ``chip``) on
        its own."""
        prev = self._ins[v]
        if event.ins is not None:
            self._ins[v] = event.ins
        ins = self._ins[v]
        if ins is None and any(k == "run" and val[1] is not None for k, val in event.ops):
            raise PlayError(f"voice {v + 1}: !run at tick {s} on a voice with no instrument")
        if not ins or not ins.restart:
            return [(s, (v, event, transpose, ins, None, None))]
        *ahead, (last, _) = ins.restart
        if event.legato:
            return [(max(s - _lookahead(ins), 0), (v, event, transpose, ins, None, None))]
        if ahead and prev is not None:
            ahead[0] = (ahead[0][0], prev.restart[0][1] if prev.restart else 0)
        if isinstance(event.note, int):
            if ahead:
                ahead[-1] = (_with_envelope(ahead[-1][0], event.ops), ahead[-1][1])
            for k, (row, before) in enumerate(ahead):
                if s - before >= 0:
                    self._lookahead[self.song.call_of(s - before)].append((v, row, not k))
            return [(s, (v, event, transpose, ins, last, None))]
        if not ahead:
            return [(s, (v, event, transpose, ins, None, None))]
        parts = [
            (ahead[0][1], "gate", event.note),
            (ahead[-1][1], "ops", not all(map(self._chip, event.ops))),
            (0, "chip", any(map(self._chip, event.ops))),
        ]
        return [
            (max(s - before, 0), (v, event, transpose, ins, None, part))
            for before, part, has in parts
            if has
        ]

    def _hold_step(self, s: int, v: int, event: CEvent | None, last: bool):
        """The hold calls of voice ``v``'s step at sequencer tick ``s``: the call of each event
        it is, and of its read, by the restart of the voice's instrument before the step's
        event."""
        call_of, now = self.song.call_of, self.song.call_of(s)
        at = {"step": now, "last": now if last else None, "read": None}
        ins = self._ins[v]
        if event is not None and isinstance(event.note, int):
            at["note"] = None if event.legato else now
            if event.legato:
                at["legato"] = call_of(max(s - _lookahead(event.ins or ins), 0))
        if ins is not None:
            at["read"] = call_of(s - (ins.restart[0][1] if ins.restart else 0))
        for mark, events in self.song.marks.items():
            self._held[v, mark].update(at[e] - k for e, k in events if at.get(e) is not None)

    def _marked(self, v: int, mark: str, c: int) -> bool:
        """Whether call ``c`` is one of voice ``v``'s events of ``mark``."""
        rows = self.song.rows.get(mark, ())
        return c in self._held[v, mark] or any(c in self._held[v, e] for e in rows)

    def _advance(self):
        """Schedules the steps that begin by the song's horizon past the current tick."""
        song = self.song
        for tick, v, event, transpose, last in self._seq.advance(self._stick + song.horizon):
            self._hold_step(tick, v, event, last)
            if event is not None:
                for at, item in self._schedule(tick, v, event, transpose):
                    self._events[song.call_of(at)].append(item)

    def _call(self) -> bool:
        """Runs one call; False where the song has ended."""
        c, song = self.call, self.song
        self._advance()
        if self.finished:
            return False
        full = song.full is None or song.full[c % len(song.full)]
        rows, events = (self._lookahead.pop(c, ()), self._events.pop(c, ())) if full else ((), ())
        if c == self._first:
            for item in self._init:
                self._event(c, *item)
        for phase, cell in self._phases:
            if cell:
                self._fire(cell)
            elif phase in SLOT_PHASES:
                for slot in self._layers[SLOT_PHASES[phase]]:
                    for v in range(len(self.voices)):
                        self._run_slot(v, slot, c)
            elif full:
                self._voice_events(c, phase == "read", rows, events)
        for ticks in self._held.values():
            ticks.discard(c)
        for v, slot in self._skipped:
            self.voices[v].lost[slot] += 1
        self._skipped.clear()
        self.call += 1
        self._stick += bool(full)
        if self._delay:
            self._delay.append(self.registers(list(song.regs)))
            self.regs = self._delay.popleft()
        return True

    def step(self) -> list | None:
        """Plays one frame."""
        for _ in range(self.song.frame):
            if not self._call():
                return None
        self.tick += 1
        return list(self.regs) if self._delay else self.registers()

    def _voice_events(self, c: int, reading: bool, rows, events):
        """Per voice, its restart rows due at ``c`` and its events: the read rows alone where
        ``reading``, else the events with every row but those the ``read`` phase runs."""
        apart = "read" in self.song.order
        for v in range(len(self.voices)):
            for rv, row, first in rows:
                if rv == v and reading == (apart and first):
                    self._apply(v, row[2], c, True)
            for item in () if reading else events:
                if item[0] == v:
                    self._event(c, *item)

    def registers(self, regs: list | None = None) -> list:
        """The 25 register values the cells make, written to ``regs`` (by default ``regs``)."""
        regs = self.regs if regs is None else regs
        for v, voice in enumerate(self.voices):
            at = 7 * v
            regs[at] = voice.freq & 0xFF
            regs[at + 1] = voice.freq >> 8
            regs[at + 2] = voice.pw & 0xFF
            regs[at + 3] = voice.pw >> 8
            regs[at + 4] = ((voice.ctrl ^ voice.mask) & 0xFE) | (voice.ctrl & voice.gate & 1)
            regs[at + 5] = voice.ad
            regs[at + 6] = voice.sr
        chip = self.chip
        regs[21] = chip["cut"] & 7
        regs[22] = chip["cut"] >> 3
        regs[23] = chip["res"] << 4 | chip["route"]
        regs[24] = chip["mode"] | chip["vol"]
        return list(regs)

    def _start(self, v: int, slot: int, prog: CProgram | None, c: int):
        """Starts ``prog`` in program slot ``slot`` at the slot's clock, or with None stops it; a
        filter program stops the same layer of every other voice."""
        running = prog is not None and bool(prog.rows)
        if self.observe:
            self.observe("start", v, slot, prog)
        if running and self.song.kinds[slot] == FILTER:
            for other in range(len(self.voices)):
                if other != v:
                    self._stop_filter(other, (slot,))
        voice = self.voices[v]
        voice.slots[slot] = (
            [prog, 0, c - voice.lost[slot] + prog.rows[0][0]] if running else [None, 0, 0]
        )

    def _chip(self, op: tuple) -> bool:
        """Whether a command sets a song cell, or is a filter program's ``!run``."""
        key, value = op
        return key in SONG_CELLS or key == "run" and self.song.kinds[value[0]] == FILTER

    def _mods(self, v: int, cell: str) -> list:
        return self.song_mods[cell] if cell in SONG_CELLS else self.voices[v].mods[cell]

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def _end(self, v: int, cell: str, ends, command: bool | None = None, anyone=False):
        """Ends the modulators in slots ``ends`` of ``cell`` that voice ``v`` set, or ``anyone``,
        only those of commands or only those of rows where ``command`` says."""
        mods = self._mods(v, cell)
        for k in ends:
            mod = mods[k]
            if mod is not None and (anyone or mod[2] == v) and command in (None, mod[4]):
                mods[k] = None

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def _set(self, v: int, cell: str, slot: int, fn, once: bool, command: bool, fresh=False):
        """Binds ``slot`` of ``cell`` to modulator ``fn`` by a command or a row, at phi 0 but
        where it is bound to ``fn`` already and the binding is no ``fresh`` one of a note's
        instrument nor a ``once`` one."""
        mods = self._mods(v, cell)
        if self.observe:
            self.observe("bind", v, cell, slot, command, fn, once, fresh)
        mod = mods[slot]
        if not (fresh or once) and mod is not None and mod[0] is fn and not mod[5]:
            mod[2], mod[4] = v, command
        else:
            mods[slot] = [fn, 0, v, f"{self.song.slots[cell][slot]}.{cell}", command, once]
        if cell in SONG_CELLS:
            self.owner[cell] = v
        elif cell == "freq":
            self.voices[v].freq_hold = False

    def _stop_filter(self, v: int, slots):
        for slot in slots:
            self.voices[v].slots[slot][0] = None
        self._end(v, "cut", range(len(self.song_mods["cut"])), False)

    def _stop(self, v: int, cell: str, value, ends: tuple):
        """Cell ``cell``'s ``reset``: phi of the modulators in slots ``ends`` back at 0; ``none`` or
        ``hold``: ends them (a song cell's only where voice ``v`` set them, unless the song
        owns it); ``hold`` also stops the cell's program slot and holds the frequency."""
        voice = self.voices[v]
        if self.observe:
            self.observe("stop", v, cell, value, ends)
        if value == RESET:
            for mod in (self._mods(v, cell)[k] for k in ends):
                if mod is not None:
                    mod[1] = 0
            return
        shared = cell in self._shared
        self._end(v, cell, ends, None, shared)
        if value == HOLD:
            if cell == "pw":
                for slot in self._layers[PULSE]:
                    voice.slots[slot][0] = None
            elif cell == "cut":
                for u in range(len(self.voices)) if shared else (v,):
                    self._stop_filter(u, self._layers[FILTER])
        if cell == "freq":
            voice.freq_hold = value == HOLD

    def _run_slot(self, v: int, kind: int, c: int):
        """Runs the rows of voice ``v``'s program slot ``kind`` due by its clock at call ``c``,
        none where the slot is paused on one of the voice's events."""
        voice = self.voices[v]
        if (mark := self._pauses.get(kind)) and self._marked(v, mark, c):
            self._skipped.append((v, kind))
            return
        slots, seen, control = voice.slots, set(), self.song.kinds[kind] == CONTROL
        clock = c - voice.lost[kind]
        while (slot := slots[kind])[0] is not None and slot[2] <= clock:
            prog, index = slot[0], slot[1]
            if (id(prog), index) in seen:
                raise PlayError(f"voice {v + 1}: a program loops in no time at call {c}")
            seen.add((id(prog), index))
            _, hold, ops = prog.rows[index]
            if self.observe:
                self.observe("row", v, kind)
            self._apply(v, ops, c, False, prog.fresh)
            if control:
                self._note_row(v, ops, c)
            if slots[kind] is not slot or slot[0] is None:
                continue
            index += 1
            if index == len(prog.rows):
                if prog.loop is None:
                    slot[0] = None
                    break
                index = prog.loop
            slot[1], slot[2] = index, clock + hold + prog.rows[index][0]

    def _note_row(self, v: int, ops: tuple, c: int):
        """Records the ``row`` and ``row.KEY`` events of a control program row applied at ``c``."""
        keys = {EVENT_KEY.get(key, key) for key, _ in ops}
        events = {"row." + key for key in keys}
        if keys - {"ctrl", "gate"}:
            events.add("row")
        for event in events:
            self._held[v, event].add(c)

    # pylint: disable-next=too-many-branches,too-many-arguments,too-many-positional-arguments
    def _apply(self, v: int, ops: tuple, c: int, first: bool, fresh=False, command=False):
        """Keys ``ops`` of a row or the commands of an event, in vocabulary order; ``first``
        marks a restart's first-tick row, ``fresh`` an instrument field's program, whose
        bindings restart phi."""
        voice = self.voices[v]
        if self.observe:
            self.observe("enter", "apply", v, first, command)
        for key, value in ops:
            if key == "mask":
                voice.mask = value & 0xFE
                voice.gate |= first
            elif key == "gate":
                voice.gate = 1 if value else 0
            elif key == "note":
                if value[0] == "rel":
                    voice.relative, voice.absolute = value[1], None
                else:
                    voice.absolute = value[1]
            elif key in ("route_on", "route_off"):
                bits = (1 << v | value) & 15
                route = self.chip["route"]
                self.chip["route"] = route | bits if key == "route_on" else route & ~bits
            elif key == "run":
                self._start(v, value[0], value[1], c)
            elif isinstance(value[0], int):
                voice.gate |= first and key == "ctrl" and written(value)
                self._set(v, key, *value, command, fresh)
            else:
                self._stop(v, key, *value)
        if self.observe:
            self.observe("leave", "apply")

    def _event(
        self, c: int, v: int, event: CEvent, transpose: int, ins, last, part
    ):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Event ``event``, or its ``part``: ``gate`` its gate alone, ``ops`` its voice's
        commands, ``chip`` its song cells' ones."""
        voice = self.voices[v]
        if self.observe:
            self.observe("event", v, event, ins)
        if last is not None:
            self._apply(v, last[2], c, True)
        if isinstance(event.note, int):
            voice.note = event.note + transpose
            if not event.legato:
                voice.relative, voice.absolute, voice.gate = 0, None, 1
                if ins is not None:
                    self._instrument(v, ins, event, c)
        elif event.note is not None and part not in ("ops", "chip"):
            voice.gate = 1 if event.note == "on" else 0
        for op in event.ops if part != "gate" else ():
            if part is None or (part == "chip") == self._chip(op):
                self._apply(v, (op,), c, False, command=True)

    def _instrument(self, v: int, ins: CInstrument, event: CEvent, c: int):
        voice = self.voices[v]
        if self.observe:
            self.observe("enter", "instrument", v, ins)
        if ins.ctrl is not None:
            self._start(v, CONTROL, ins.ctrl, c)
        if ins.pw is not None and not event.keep_pw:
            self._start(v, PULSE, ins.pw, c)
        if ins.filter is not None and not event.keep_filter:
            self._start(v, FILTER, ins.filter, c)
        keep = {PULSE: event.keep_pw, FILTER: event.keep_filter}
        for slot, prog in ins.layers:
            if not keep.get(self.song.kinds[slot]):
                self._start(v, slot, prog, c)
        for cell, op in ins.binds:
            if isinstance(op[0], int):
                self._set(v, cell, *op, False, fresh=True)
            else:
                self._stop(v, cell, *op)
        voice.freq_hold = False
        if self.observe:
            self.observe("leave", "instrument")

    def _chain(self, mods: list, value: int, cell: str, owner: int = 0) -> int | None:
        """The cell's value ``value`` after its modulators ``mods`` fire, or None where it has
        none. A slot fires on the value the slot before left unless it holds on its voice's events,
        its phi first back at 0 on its reset events, and a once binding is then unbound."""
        if not any(mods):
            return None
        c, mask = self.call, MASKS[cell]
        for k, mod in enumerate(mods):
            if mod is None:
                continue
            if self._marked(mod[2], "reset " + mod[3], c):
                mod[1] = 0
                if self.observe:
                    self.observe("rewind", mod[2], mod[3])
            if not self._marked(mod[2], "hold " + mod[3], c):
                fn, notes = mod[0], self._notes(mod[2])
                if type(fn) is Routine:  # pylint: disable=unidiomatic-typecheck
                    state = self.song_state if cell in SONG_CELLS else self.voices[mod[2]].state
                    out = fn(value, mod[1], *notes, state[cell]) & mask
                else:
                    out = fn(value, mod[1], *notes) & mask
                if self.observe:
                    self.observe("fire", cell, owner, k, mod[2], fn, mod[1], value, out, *notes)
                value = out
                mod[1] += 1
                if mod[5]:
                    mods[k] = None
        return value

    def _notes(self, v: int) -> tuple:
        """``(N, n)`` of voice ``v``, each 0 where it has none."""
        voice = self.voices[v]
        return voice.sounding or 0, voice.note or 0

    def _fire(self, cell: str):
        """Fires ``cell`` in its phase: a voice's cell for each voice in order, a song cell once,
        a shared one only where it has an owner."""
        if cell in SONG_CELLS:
            if cell in self._shared and self.owner[cell] is None:
                return
            out = self._chain(self.song_mods[cell], self.chip[cell], cell)
            if out is not None:
                self.chip[cell] = out
            return
        for v, voice in enumerate(self.voices):
            if cell == "freq":
                self._fire_freq(v)
            elif (out := self._chain(voice.mods[cell], getattr(voice, cell), cell, v)) is not None:
                setattr(voice, cell, out)
            if self.observe:
                self.observe("cell", cell, v)

    def _fire_freq(self, v: int):
        voice = self.voices[v]
        n = voice.sounding
        out = self._chain(voice.mods["freq"], voice.freq, "freq", v)
        if out is not None:
            voice.freq = out
        elif n is not None and not voice.freq_hold:
            voice.freq = self.song.table(n)


def _lookahead(ins: CInstrument | None) -> int:
    """The ticks before an event of its restart's last lookahead row, 0 for none."""
    return ins.restart[-2][1] if ins is not None and ins.restart and len(ins.restart) > 1 else 0


def _with_envelope(row: tuple, ops: tuple) -> tuple:
    """Restart row ``row`` with a note's plain ``ad``, ``sr`` and ``ctrl`` ``ops`` after its own
    keys."""
    wait, hold, keys = row
    return wait, hold, keys + tuple((k, v) for k, v in ops if k in ENVELOPE and written(v))


def play(source, ticks: int | None = None, progress: bool = False) -> np.ndarray:
    """The registers at the end of each frame of a document's text or Song, shape ``(frames,
    25)``: to the song's end when ``ticks`` is None (a looping song needs ``ticks``), and fewer
    rows where the song ends first."""
    song = load(source) if isinstance(source, str) else source
    if ticks is None and song.looping:
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
