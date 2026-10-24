"""The tick machine: a canonical document played to the 25 SID registers' state per tick."""

import itertools
from collections import defaultdict

import numpy as np
from tqdm import tqdm

from unitracker_player.code import Routine
from unitracker_player.compiler import (
    CELLS,
    CHIP,
    CONTROL,
    CUT_MASK,
    ENVELOPE,
    FILTER,
    FREQ_MASK,
    HOLD,
    PULSE,
    PW_MASK,
    REGISTERS,
    RESET,
    SLOT_PHASES,
    CEvent,
    CInstrument,
    CProgram,
    Song,
    compile_document,
    load,
)
from unitracker_player.sequencer import PlayError, Sequencer

__all__ = [
    "REGISTERS",
    "Player",
    "PlayError",
    "Song",
    "compile_document",
    "load",
    "play",
]


class _Voice:  # pylint: disable=too-many-instance-attributes
    """A voice's cells, note state, modulators and program slots; ``freq_mods`` and
    ``pw_mods`` hold its cells' modulator slots, each modulator ``[fn, phi, voice, slot.cell, by
    command]``."""

    def __init__(self, widths: dict, state: dict):
        self.freq = self.pw = self.ctrl = self.mask = self.gate = self.ad = self.sr = 0
        self.note = self.absolute = None
        self.relative = 0
        self.freq_mods, self.pw_mods = [None] * widths["freq"], [None] * widths["pw"]
        self.freq_hold = False
        self.slots = [[None, 0, 0] for _ in range(3)]
        self.state = {cell: bytearray(state.get(cell, 0)) for cell in ("freq", "pw")}

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
        widths = {cell: len(n) for cell, n in song.slots.items()}
        self.voices = [_Voice(widths, song.state) for _ in range(song.voices)]
        self.cut_mods = [None] * widths["cut"]
        self.cut_state = bytearray(song.state.get("cut", 0))
        self.cut = self.res = self.route = self.mode = self.vol = 0
        self._preset(song.regs)
        self._seq = Sequencer(song)
        self._ins = [None] * song.voices
        self._events = defaultdict(list)
        self._lookahead = defaultdict(list)
        self._held = defaultdict(set)
        self._cells = ()
        self._phases = [
            phase
            for cells, names in itertools.groupby(song.order, CELLS.__contains__)
            for phase in ([("fire", tuple(names))] if cells else ((n, None) for n in names))
        ]
        self._init = [i for v, e in enumerate(song.init) for _, i in self._schedule(0, v, e, 0)]
        for cell, bound in song.defaults.items():
            for v in range(song.voices if cell != "cut" else 1):
                for slot, fn in bound:
                    self._set(v, cell, slot, fn, 0, False)

    def _preset(self, regs: tuple):
        """The cells whose registers are ``regs``, the register file ``regs``."""
        self.regs = list(regs)
        for v, voice in enumerate(self.voices):
            at = 7 * v
            voice.freq = regs[at] | regs[at + 1] << 8
            voice.pw = regs[at + 2] | (regs[at + 3] & 15) << 8
            voice.ctrl, voice.gate = regs[at + 4], regs[at + 4] & 1
            voice.ad, voice.sr = regs[at + 5], regs[at + 6]
        self.cut = regs[21] & 7 | regs[22] << 3
        self.res, self.route = regs[23] >> 4, regs[23] & 15
        self.mode, self.vol = regs[24] & 0xF0, regs[24] & 15

    @property
    def finished(self) -> bool:
        """Whether the song has ended at the current tick."""
        return self._seq.end is not None and self.tick >= self._seq.end

    def _schedule(self, t: int, v: int, event: CEvent, transpose: int) -> list:
        """The ``(tick, event item)`` pairs of an event: a note-less one split at its
        restart's lookahead, its gate (part ``gate``) on the first row's tick, its voice's
        commands (part ``ops``) on the last's and its filter and volume ones (part ``chip``) on
        its own."""
        prev = self._ins[v]
        if event.ins is not None:
            self._ins[v] = event.ins
        ins = self._ins[v]
        if ins is None and any(k == "run" and val is not None for k, val in event.ops):
            raise PlayError(f"voice {v + 1}: !run at tick {t} on a voice with no instrument")
        if not ins or not ins.restart:
            return [(t, (v, event, transpose, ins, None, None))]
        *ahead, (last, _) = ins.restart
        if event.legato:
            return [(max(t - _lookahead(ins), 0), (v, event, transpose, ins, None, None))]
        if ahead and prev is not None:
            ahead[0] = (ahead[0][0], prev.restart[0][1] if prev.restart else 0)
        if isinstance(event.note, int):
            if ahead:
                ahead[-1] = (_with_envelope(ahead[-1][0], event.ops), ahead[-1][1])
            for k, (row, before) in enumerate(ahead):
                if t - before >= 0:
                    self._lookahead[t - before].append((v, row, not k))
            return [(t, (v, event, transpose, ins, last, None))]
        if not ahead:
            return [(t, (v, event, transpose, ins, None, None))]
        parts = [
            (ahead[0][1], "gate", event.note),
            (ahead[-1][1], "ops", not all(map(_chip, event.ops))),
            (0, "chip", any(map(_chip, event.ops))),
        ]
        return [
            (max(t - before, 0), (v, event, transpose, ins, None, part))
            for before, part, has in parts
            if has
        ]

    def _hold_step(self, t: int, v: int, event: CEvent | None, last: bool):
        """The hold ticks of voice ``v``'s step at tick ``t``: the tick of each event it is, and
        of its read, by the restart of the voice's instrument before the step's event."""
        at = {"step": t, "last": t if last else None, "read": None}
        ins = self._ins[v]
        if event is not None and isinstance(event.note, int):
            at["note"] = None if event.legato else t
            at["legato"] = max(t - _lookahead(event.ins or ins), 0) if event.legato else None
        if ins is not None:
            at["read"] = t - (ins.restart[0][1] if ins.restart else 0)
        for mark, events in self.song.marks.items():
            self._held[v, mark].update(at[e] - k for e, k in events if at.get(e) is not None)

    def _marked(self, v: int, mark: str, t: int) -> bool:
        """Whether tick ``t`` is one of voice ``v``'s events of ``mark``."""
        rows = self.song.rows.get(mark, ())
        return t in self._held[v, mark] or any(t in self._held[v, e] for e in rows)

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
        for phase, arg in self._phases:
            if phase == "fire":
                self._cells = arg
                self._fire()
            elif phase in SLOT_PHASES:
                for v in range(len(self.voices)):
                    self._run_slot(v, SLOT_PHASES[phase], t)
            else:
                self._voice_events(t, phase == "read", rows, events)
        for ticks in self._held.values():
            ticks.discard(t)
        self.tick += 1
        return self.registers()

    def _voice_events(self, t: int, reading: bool, rows, events):
        """Per voice, its restart rows due at ``t`` and its events: the read rows alone where
        ``reading``, else the events with every row but those the ``read`` phase runs."""
        apart = "read" in self.song.order
        for v in range(len(self.voices)):
            for rv, row, first in rows:
                if rv == v and reading == (apart and first):
                    self._row(v, row, t)
            for item in () if reading else events:
                if item[0] == v:
                    self._event(t, *item)

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
        if running and kind == FILTER:
            for other in range(len(self.voices)):
                if other != v:
                    self._stop_filter(other)
        self.voices[v].slots[kind] = [prog, 0, t + prog.rows[0][0]] if running else [None, 0, 0]

    def _mods(self, v: int, cell: str) -> list:
        return self.cut_mods if cell == "cut" else getattr(self.voices[v], cell + "_mods")

    def _end(self, v: int, cell: str, ends, command: bool | None = None):
        """Ends the modulators in slots ``ends`` of ``cell`` that voice ``v`` set, only those of
        commands or only those of rows where ``command`` says."""
        mods = self._mods(v, cell)
        for k in ends:
            mod = mods[k]
            if mod is not None and mod[2] == v and command in (None, mod[4]):
                mods[k] = None

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def _set(self, v: int, cell: str, slot: int, fn, command: bool, fresh=False):
        """Binds ``slot`` of ``cell`` to modulator ``fn`` by a command or a row, at phi 0 but
        where it is bound to ``fn`` already and the binding is no ``fresh`` one of a note's
        instrument."""
        mods = self._mods(v, cell)
        if not fresh and mods[slot] is not None and mods[slot][0] is fn:
            mods[slot][2], mods[slot][4] = v, command
        else:
            mods[slot] = [fn, 0, v, f"{self.song.slots[cell][slot]}.{cell}", command]
        if cell == "freq":
            self.voices[v].freq_hold = False

    def _stop_filter(self, v: int):
        self.voices[v].slots[FILTER][0] = None
        self._end(v, "cut", range(len(self.cut_mods)), False)

    def _stop(self, v: int, cell: str, value, ends: tuple):
        """Cell ``cell``'s ``reset``: phi of the modulators in slots ``ends`` back at 0; ``none`` or
        ``hold``: ends them (the cutoff's only where voice ``v`` set them); ``hold`` also stops
        the cell's program slot and holds the frequency."""
        voice = self.voices[v]
        if value == RESET:
            for mod in (self._mods(v, cell)[k] for k in ends):
                if mod is not None:
                    mod[1] = 0
            return
        self._end(v, cell, ends)
        if value == HOLD:
            if cell == "pw":
                voice.slots[PULSE][0] = None
            elif cell == "cut":
                self._stop_filter(v)
        if cell == "freq":
            voice.freq_hold = value == HOLD

    def _run_slot(self, v: int, kind: int, t: int):
        slots, seen = self.voices[v].slots, set()
        while (slot := slots[kind])[0] is not None and slot[2] <= t:
            prog, index = slot[0], slot[1]
            if (id(prog), index) in seen:
                raise PlayError(f"voice {v + 1}: a program loops in no time at tick {t}")
            seen.add((id(prog), index))
            _, hold, ops = prog.rows[index]
            self._apply(v, ops, t, False, prog.fresh)
            if kind == CONTROL:
                keys = {key for key, _ in ops}
                events = {"row." + key for key in keys}
                if keys - {"ctrl", "gate"}:
                    events.add("row")
                for event in events:
                    self._held[v, event].add(t)
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
    def _apply(self, v: int, ops: tuple, t: int, first: bool, fresh=False):
        """A row's keys, in vocabulary order; ``first`` marks a restart's first-tick row,
        ``fresh`` an instrument field's program, whose bindings restart phi."""
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
            elif key in CELLS:
                if isinstance(value[0], int):
                    self._set(v, key, *value, False, fresh)
                else:
                    self._stop(v, key, *value)
            elif key == "run":
                self._start(v, value[0], value[1], t)
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
        """Event ``event``, or its ``part``: ``gate`` its gate alone, ``ops`` its voice's
        commands, ``chip`` its filter and volume ones."""
        voice = self.voices[v]
        if last is not None:
            self._row(v, last, t, True)
        if isinstance(event.note, int):
            voice.note = event.note + transpose
            if not event.legato:
                voice.relative, voice.absolute, voice.gate = 0, None, 1
                if ins is not None:
                    self._instrument(v, ins, event, t)
        elif event.note is not None and part not in ("ops", "chip"):
            voice.gate = 1 if event.note == "on" else 0
        for key, value in event.ops if part != "gate" else ():
            if part is None or (part == "chip") == _chip((key, value)):
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
        for slot, value in ins.freq or ():
            if isinstance(slot, int):
                self._set(v, "freq", slot, value, False, fresh=True)
            else:
                self._stop(v, "freq", slot, value)
        voice.freq_hold = False

    def _command(self, v: int, key: str, value, t: int):
        voice = self.voices[v]
        if key == "run":
            if value is None:
                voice.slots[CONTROL][0] = None
            else:
                self._start(v, value[0], value[1], t)
        elif key in CELLS:
            if isinstance(value[0], int):
                self._set(v, key, *value, True)
            else:
                self._stop(v, key, *value)
        elif key in ("ad", "sr"):
            setattr(voice, key, value & 0xFF)
        elif key == "ctrl":
            voice.ctrl = value & 0xFF
        elif key == "resfilt":
            self.res, self.route = value[0] & 15, value[1] & 15
        else:
            self._global(v, key, value)

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def _chain(self, t: int, mods: list, value: int, mask: int, cell: str) -> int | None:
        """The cell's value ``value`` after its modulators ``mods`` fire, or None where it has
        none. Each slot in order fires on the value the slot before it left, unless it holds on
        the events of the voice that set it, its phi first back at 0 on its reset events. A code
        modulator runs on the state of ``cell`` of the voice that set it (the one ``cut``
        state)."""
        if not any(mods):
            return None
        for mod in mods:
            if mod is None:
                continue
            if self._marked(mod[2], "reset " + mod[3], t):
                mod[1] = 0
            if not self._marked(mod[2], "hold " + mod[3], t):
                fn, notes = mod[0], self._notes(mod[2])
                if type(fn) is Routine:  # pylint: disable=unidiomatic-typecheck
                    state = self.cut_state if cell == "cut" else self.voices[mod[2]].state[cell]
                    value = fn(value, mod[1], *notes, state) & mask
                else:
                    value = fn(value, mod[1], *notes) & mask
                mod[1] += 1
        return value

    def _notes(self, v: int) -> tuple:
        """``(N, n)`` of voice ``v``, each 0 where it has none."""
        voice = self.voices[v]
        return voice.sounding or 0, voice.note or 0

    def _fire(self):
        """Fires the cells of the modulator phases this pass runs, ``_cells``, in their order."""
        t = self.tick
        for cell in self._cells:
            if cell == "cut":
                out = self._chain(t, self.cut_mods, self.cut, CUT_MASK, "cut")
                self.cut = self.cut if out is None else out
            elif cell == "pw":
                for voice in self.voices:
                    out = self._chain(t, voice.pw_mods, voice.pw, PW_MASK, "pw")
                    voice.pw = voice.pw if out is None else out
            else:
                for vix in range(len(self.voices)):
                    self._fire_freq(t, vix)

    def _fire_freq(self, t: int, vix: int):
        voice = self.voices[vix]
        n = voice.sounding
        out = self._chain(t, voice.freq_mods, voice.freq, FREQ_MASK, "freq")
        if out is not None:
            voice.freq = out
        elif n is not None and not voice.freq_hold:
            voice.freq = self.song.table(n)


def _lookahead(ins: CInstrument | None) -> int:
    """The ticks before an event of its restart's last lookahead row, 0 for none."""
    return ins.restart[-2][1] if ins is not None and ins.restart and len(ins.restart) > 1 else 0


def _with_envelope(row: tuple, ops: tuple) -> tuple:
    """Restart row ``row`` with a note's !ad and !sr ``ops`` after its own keys."""
    wait, hold, keys = row
    first = ((k, (v, None) if k == "ctrl" else v) for k, v in ops if k in ENVELOPE)
    return wait, hold, keys + tuple(first)


def _chip(op: tuple) -> bool:
    """Whether a command sets the filter or the volume: of :data:`CHIP`, or a filter program's
    ``!run``."""
    key, value = op
    return key in CHIP or key == "run" and value is not None and value[0] == FILTER


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
