//! The call machine: a compiled document played to the 25 SID registers' state per frame.

use std::collections::VecDeque;

use crate::expr::{self};
use crate::machine::Machine;
use crate::sequencer::{Sequencer, Step};
use crate::spec::*;

type Res<T> = Result<T, String>;

#[derive(Clone, Copy)]
struct Mod {
    func: i32,
    phi: i64,
    by: u8,
    command: bool,
    once: bool,
}

const NO_MOD: Mod = Mod {
    func: -1,
    phi: 0,
    by: 0,
    command: false,
    once: false,
};

#[derive(Clone, Copy)]
struct Slot {
    prog: i32,
    index: usize,
    due: i64,
    epoch: u32,
}

const IDLE: Slot = Slot {
    prog: -1,
    index: 0,
    due: 0,
    epoch: 0,
};

struct Voice {
    freq: i64,
    pw: i64,
    ctrl: i64,
    ad: i64,
    sr: i64,
    mask: i64,
    gate: i64,
    env: i32,
    note: Option<i64>,
    absolute: Option<i64>,
    relative: i64,
    freq_hold: bool,
    slots: Vec<Slot>,
    lost: Vec<i64>,
    state: [Vec<u8>; CELLS],
}

impl Voice {
    fn control(&self) -> i64 {
        ((self.ctrl ^ self.mask) & 0xFE) | (self.ctrl & self.gate & 1)
    }

    fn sounding(&self) -> Option<i64> {
        match (self.absolute, self.note) {
            (Some(a), _) => Some(a),
            (None, Some(n)) => Some(n + self.relative),
            _ => None,
        }
    }
}

/// How the ops of a row or an event apply at call `call`.
#[derive(Clone, Copy)]
struct How {
    call: i64,
    first: bool,
    fresh: bool,
    command: bool,
}

/// A binding of a slot of a cell to a modulator.
#[derive(Clone, Copy)]
struct Bind {
    cell: usize,
    slot: usize,
    func: i32,
    once: bool,
}

impl Bind {
    fn of(op: &Op) -> Bind {
        Bind {
            cell: op.cell,
            slot: op.a as usize,
            func: op.b as i32,
            once: op.once,
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Part {
    Whole,
    Gate,
    Ops,
    Chip,
}

#[derive(Clone, Copy)]
struct Item {
    voice: usize,
    event: usize,
    transpose: i64,
    ins: i32,
    last: i32,
    part: Part,
}

#[derive(Clone, Copy)]
struct Look {
    voice: usize,
    row: usize,
    extra: i32,
    first: bool,
}

/// Plays a [`Spec`] one frame at a time. Every method taking `spec` takes the one the player was
/// made from.
pub struct Player {
    call: i64,
    stick: i64,
    pub tick: i64,
    first: i64,
    voices: Vec<Voice>,
    chip: [i64; CELLS],
    owner: [i32; CELLS],
    song_state: [Vec<u8>; CELLS],
    mods: Vec<Mod>,
    stack: Vec<i64>,
    mod_off: [usize; CELLS],
    mod_total: usize,
    seq: Sequencer,
    ins_of: Vec<i32>,
    events: Vec<Vec<Item>>,
    looks: Vec<Vec<Look>>,
    ring: i64,
    held: Vec<u64>,
    words: usize,
    row_at: Vec<i64>,
    skipped: Vec<(usize, usize)>,
    seen: Vec<(usize, usize)>,
    delay: VecDeque<[u8; REGISTERS]>,
    regs: [u8; REGISTERS],
    init: Vec<Item>,
    machine: Option<Machine>,
    steps: Vec<Step>,
    placed: Vec<(i64, Item)>,
    befores: Vec<i64>,
    pub segments: Vec<Vec<[i64; 3]>>,
    /// The firings, as `[call, cell, slot, voice, phi, value, out]`, where `Some`.
    pub fired: Option<Vec<[i64; 7]>>,
}

impl Player {
    pub fn new(spec: &Spec, machine: Option<Machine>) -> Res<Player> {
        let nv = spec.voices;
        let span = (spec.horizon.max(0) + 4) * spec.full.len().max(1) as i64 + spec.frame as i64;
        let ring = (2 * span + 64).unsigned_abs().next_power_of_two() as i64;
        let words = (ring as usize).div_ceil(64);
        let mut mod_off = [0usize; CELLS];
        for cell in 1..CELLS {
            mod_off[cell] = mod_off[cell - 1] + spec.nslots[cell - 1];
        }
        let mod_total: usize = spec.nslots.iter().sum();
        let nk = spec.kinds.len();
        let voices = (0..nv)
            .map(|_| Voice {
                freq: 0,
                pw: 0,
                ctrl: 0,
                ad: 0,
                sr: 0,
                mask: 0,
                gate: 0,
                env: NONE,
                note: None,
                absolute: None,
                relative: 0,
                freq_hold: false,
                slots: vec![IDLE; nk],
                lost: vec![0; nk],
                state: std::array::from_fn(|c| vec![0; spec.state[c]]),
            })
            .collect();
        let mut p = Player {
            call: 0,
            stick: 0,
            tick: 0,
            first: spec.call_of(0),
            voices,
            chip: [0; CELLS],
            owner: [NONE; CELLS],
            song_state: std::array::from_fn(|c| vec![0; spec.state[c]]),
            mods: vec![NO_MOD; (nv + 1) * mod_total],
            stack: vec![0; spec.depth],
            mod_off,
            mod_total,
            seq: Sequencer::new(spec),
            ins_of: vec![NONE; nv],
            events: vec![Vec::new(); ring as usize],
            looks: vec![Vec::new(); ring as usize],
            ring,
            held: vec![0; spec.marks.len() * nv * words],
            words,
            row_at: vec![-1; nv * spec.row_events],
            skipped: Vec::new(),
            seen: Vec::new(),
            delay: VecDeque::new(),
            regs: spec.regs,
            init: Vec::new(),
            machine,
            steps: Vec::new(),
            placed: Vec::new(),
            befores: Vec::new(),
            segments: vec![Vec::new(); nv],
            fired: None,
        };
        p.preset(spec);
        p.delay = (0..spec.lag).map(|_| spec.regs).collect();
        let mut placed = Vec::new();
        for (v, &e) in spec.init.iter().enumerate() {
            p.schedule(spec, 0, v, e, 0, &mut placed)?;
        }
        p.init = placed.into_iter().map(|(_, item)| item).collect();
        for &(cell, slot, func, once) in &spec.defaults {
            for v in 0..(if song_cell(cell) { 1 } else { nv }) {
                p.set_mod(
                    v,
                    Bind {
                        cell,
                        slot,
                        func,
                        once,
                    },
                    false,
                    false,
                );
            }
        }
        Ok(p)
    }

    fn preset(&mut self, spec: &Spec) {
        let regs = spec.regs.map(i64::from);
        for (v, voice) in self.voices.iter_mut().enumerate() {
            let at = 7 * v;
            voice.freq = regs[at] | regs[at + 1] << 8;
            voice.pw = regs[at + 2] | (regs[at + 3] & 15) << 8;
            voice.ctrl = regs[at + 4];
            voice.gate = regs[at + 4] & 1;
            voice.ad = regs[at + 5];
            voice.sr = regs[at + 6];
        }
        self.chip[CUT] = regs[21] & 7 | regs[22] << 3;
        self.chip[RES] = regs[23] >> 4;
        self.chip[ROUTE] = regs[23] & 15;
        self.chip[MODE] = regs[24] & 0xF0;
        self.chip[VOL] = regs[24] & 15;
    }

    /// Fires routine `index` of the machine on `(v, phi, note, event)` with `state`, updated in
    /// place; the 16-bit value it returns.
    pub fn fire_routine(
        &mut self,
        spec: &Spec,
        index: usize,
        inputs: [u8; 6],
        state: &mut [u8],
    ) -> Res<i64> {
        let routine = spec.routines.get(index).ok_or("no such routine")?;
        self.machine
            .as_mut()
            .ok_or("no machine for a code modulator")?
            .fire(index, routine, inputs, state)
    }

    pub fn finished(&self, spec: &Spec) -> bool {
        self.seq
            .end
            .is_some_and(|end| self.call >= spec.call_of(end))
    }

    fn mod_at(&self, v: usize, cell: usize, slot: usize) -> usize {
        let owner = if song_cell(cell) {
            self.voices.len()
        } else {
            v
        };
        owner * self.mod_total + self.mod_off[cell] + slot
    }

    fn ring_at(&self, call: i64) -> usize {
        (call & (self.ring - 1)) as usize
    }

    fn place(&self, call: i64) -> Res<Option<usize>> {
        if call < self.call {
            return Ok(None);
        }
        if call >= self.call + self.ring {
            return Err("the lookahead outruns the ring".into());
        }
        Ok(Some(self.ring_at(call)))
    }

    fn held_add(&mut self, mark: usize, v: usize, call: i64) -> Res<()> {
        if self.place(call)?.is_some() {
            let bit = self.ring_at(call);
            let base = (mark * self.voices.len() + v) * self.words;
            self.held[base + bit / 64] |= 1 << (bit % 64);
        }
        Ok(())
    }

    fn marked(&self, spec: &Spec, v: usize, mark: i32, c: i64) -> bool {
        if mark < 0 {
            return false;
        }
        let mark = mark as usize;
        let bit = self.ring_at(c);
        let base = (mark * self.voices.len() + v) * self.words;
        if self.held[base + bit / 64] >> (bit % 64) & 1 != 0 {
            return true;
        }
        let nr = spec.row_events;
        spec.marks[mark]
            .rows
            .iter()
            .any(|&e| self.row_at[v * nr + e as usize] == c)
    }

    fn lookahead(&self, spec: &Spec, ins: i32) -> i64 {
        if ins < 0 {
            return 0;
        }
        let rows = spec.rows_of(spec.inss[ins as usize].restart);
        if rows.len() > 1 {
            rows[rows.len() - 2].before
        } else {
            0
        }
    }

    fn first_before(&self, spec: &Spec, ins: i32) -> i64 {
        let rows = spec.rows_of(spec.inss[ins as usize].restart);
        rows.first().map_or(0, |r| r.before)
    }

    fn hold_step(
        &mut self,
        spec: &Spec,
        s: i64,
        v: usize,
        event: Option<usize>,
        last: bool,
    ) -> Res<()> {
        let now = spec.call_of(s);
        let ins = self.ins_of[v];
        let mut at: [Option<i64>; 6] = [None; 6];
        at[0] = Some(now);
        at[1] = last.then_some(now);
        if let Some(e) = event {
            let ev = &spec.events[e];
            at[3] = Some(now);
            if let Note::Pitch(_) = ev.note {
                at[4] = (!ev.legato).then_some(now);
                if ev.legato {
                    let used = if ev.ins >= 0 { ev.ins } else { ins };
                    at[5] = Some(spec.call_of((s - self.lookahead(spec, used)).max(0)));
                }
            }
        }
        if ins >= 0 {
            at[2] = Some(spec.call_of(s - self.first_before(spec, ins)));
        }
        for (m, mark) in spec.marks.iter().enumerate() {
            for &(kind, k) in &mark.events {
                if let Some(Some(x)) = at.get(kind as usize) {
                    self.held_add(m, v, x - k)?;
                }
            }
        }
        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    fn schedule(
        &mut self,
        spec: &Spec,
        s: i64,
        v: usize,
        eid: usize,
        transpose: i64,
        out: &mut Vec<(i64, Item)>,
    ) -> Res<()> {
        let ev = &spec.events[eid];
        let prev = self.ins_of[v];
        if ev.ins >= 0 {
            self.ins_of[v] = ev.ins;
        }
        let ins = self.ins_of[v];
        if ins < 0 && ev.run_program {
            return Err(format!(
                "voice {}: !run at tick {s} on a voice with no instrument",
                v + 1
            ));
        }
        let item = |last: i32, part: Part| Item {
            voice: v,
            event: eid,
            transpose,
            ins,
            last,
            part,
        };
        let restart = if ins >= 0 {
            spec.inss[ins as usize].restart
        } else {
            (0, 0)
        };
        if restart.1 == 0 {
            out.push((s, item(NONE, Part::Whole)));
            return Ok(());
        }
        let last = (restart.0 + restart.1 - 1) as i32;
        let ahead = restart.1 - 1;
        if ev.legato {
            out.push((
                (s - self.lookahead(spec, ins)).max(0),
                item(NONE, Part::Whole),
            ));
            return Ok(());
        }
        self.befores.clear();
        self.befores
            .extend(spec.rows_of((restart.0, ahead)).iter().map(|r| r.before));
        if ahead > 0 && prev >= 0 {
            self.befores[0] = self.first_before(spec, prev);
        }
        if let Note::Pitch(_) = ev.note {
            for k in 0..ahead {
                let before = self.befores[k];
                if s - before >= 0 {
                    let x = spec.call_of(s - before);
                    if let Some(i) = self.place(x)? {
                        self.looks[i].push(Look {
                            voice: v,
                            row: restart.0 + k,
                            extra: if k == ahead - 1 { eid as i32 } else { NONE },
                            first: k == 0,
                        });
                    }
                }
            }
            out.push((s, item(last, Part::Whole)));
            return Ok(());
        }
        if ahead == 0 {
            out.push((s, item(NONE, Part::Whole)));
            return Ok(());
        }
        let parts = [
            (
                self.befores[0],
                Part::Gate,
                matches!(ev.note, Note::On | Note::Off),
            ),
            (self.befores[ahead - 1], Part::Ops, !ev.all_chip),
            (0, Part::Chip, ev.any_chip),
        ];
        for (before, part, has) in parts {
            if has {
                out.push(((s - before).max(0), item(NONE, part)));
            }
        }
        Ok(())
    }

    fn advance(&mut self, spec: &Spec) -> Res<()> {
        let mut steps = std::mem::take(&mut self.steps);
        let mut placed = std::mem::take(&mut self.placed);
        steps.clear();
        let done = self
            .seq
            .advance(spec, self.stick + spec.horizon, &mut steps);
        let mut result = done;
        for step in &steps {
            if result.is_err() {
                break;
            }
            result = self.hold_step(spec, step.tick, step.voice, step.event, step.last);
            if let (Ok(()), Some(e)) = (&result, step.event) {
                placed.clear();
                result = self.schedule(spec, step.tick, step.voice, e, step.transpose, &mut placed);
                for (at, item) in placed.iter() {
                    if result.is_err() {
                        break;
                    }
                    result = match self.place(spec.call_of(*at)) {
                        Ok(Some(i)) => {
                            self.events[i].push(*item);
                            Ok(())
                        }
                        Ok(None) => Ok(()),
                        Err(e) => Err(e),
                    };
                }
            }
        }
        self.steps = steps;
        self.placed = placed;
        result
    }

    fn mod_slots(&self, spec: &Spec, cell: usize, mask: i64) -> impl Iterator<Item = usize> {
        let n = spec.nslots[cell];
        (0..n).filter(move |&k| mask >> k & 1 != 0)
    }

    fn end_mods(
        &mut self,
        v: usize,
        cell: usize,
        slots: impl Iterator<Item = usize>,
        command: Option<bool>,
        anyone: bool,
    ) {
        for k in slots {
            let i = self.mod_at(v, cell, k);
            let m = self.mods[i];
            if m.func >= 0
                && (anyone || m.by as usize == v)
                && command.is_none_or(|c| c == m.command)
            {
                self.mods[i].func = -1;
            }
        }
    }

    fn set_mod(&mut self, v: usize, bind: Bind, command: bool, fresh: bool) {
        let Bind {
            cell,
            slot,
            func,
            once,
        } = bind;
        let i = self.mod_at(v, cell, slot);
        let m = self.mods[i];
        if !(fresh || once) && m.func >= 0 && m.func == func && !m.once {
            self.mods[i].by = v as u8;
            self.mods[i].command = command;
        } else {
            self.mods[i] = Mod {
                func,
                phi: 0,
                by: v as u8,
                command,
                once,
            };
        }
        if song_cell(cell) {
            self.owner[cell] = v as i32;
        } else if cell == FREQ {
            self.voices[v].freq_hold = false;
        }
    }

    fn stop_filter(&mut self, spec: &Spec, v: usize) {
        for &slot in &spec.layers[FILTER as usize] {
            self.voices[v].slots[slot].prog = NONE;
        }
        self.end_mods(v, CUT, 0..spec.nslots[CUT], Some(false), false);
    }

    fn stop(&mut self, spec: &Spec, v: usize, op: &Op) {
        let cell = op.cell;
        if op.tag == tag::RESET {
            for k in self.mod_slots(spec, cell, op.a).collect::<Vec<_>>() {
                let i = self.mod_at(v, cell, k);
                if self.mods[i].func >= 0 {
                    self.mods[i].phi = 0;
                }
            }
            return;
        }
        let shared = spec.shared[cell];
        let slots: Vec<usize> = self.mod_slots(spec, cell, op.a).collect();
        self.end_mods(v, cell, slots.into_iter(), None, shared);
        if op.tag == tag::HOLD {
            if cell == PW {
                for &slot in &spec.layers[PULSE as usize] {
                    self.voices[v].slots[slot].prog = NONE;
                }
            } else if cell == CUT {
                let all = if shared { 0..spec.voices } else { v..v + 1 };
                for u in all {
                    self.stop_filter(spec, u);
                }
            }
        }
        if cell == FREQ {
            self.voices[v].freq_hold = op.tag == tag::HOLD;
        }
    }

    fn start(&mut self, spec: &Spec, v: usize, slot: usize, prog: i32, c: i64) {
        let running = prog >= 0 && spec.progs[prog as usize].rows.1 > 0;
        if running && spec.kinds[slot] == FILTER {
            for other in (0..spec.voices).filter(|&o| o != v) {
                self.voices[other].slots[slot].prog = NONE;
                self.end_mods(other, CUT, 0..spec.nslots[CUT], Some(false), false);
            }
        }
        let voice = &mut self.voices[v];
        let epoch = voice.slots[slot].epoch + 1;
        voice.slots[slot] = if running {
            let first = spec.rows[spec.progs[prog as usize].rows.0];
            Slot {
                prog,
                index: 0,
                due: c - voice.lost[slot] + first.wait,
                epoch,
            }
        } else {
            Slot { epoch, ..IDLE }
        };
    }

    fn apply(&mut self, spec: &Spec, v: usize, span: (usize, usize), how: How) -> Res<()> {
        for op in spec.ops_of(span) {
            self.apply_op(spec, v, op, how)?;
        }
        Ok(())
    }

    fn apply_op(&mut self, spec: &Spec, v: usize, op: &Op, how: How) -> Res<()> {
        match op.tag {
            tag::ENV => self.voices[v].env = op.a as i32,
            tag::MASK => {
                let voice = &mut self.voices[v];
                voice.mask = op.a & 0xFE;
                voice.gate |= how.first as i64;
            }
            tag::GATE => self.voices[v].gate = (op.a != 0) as i64,
            tag::NOTE_REL => {
                let voice = &mut self.voices[v];
                voice.relative = op.a;
                voice.absolute = None;
            }
            tag::NOTE_ABS => self.voices[v].absolute = Some(op.a),
            tag::ROUTE_ON | tag::ROUTE_OFF => {
                let bits = ((1i64 << v) | op.a) & 15;
                let route = self.chip[ROUTE];
                self.chip[ROUTE] = if op.tag == tag::ROUTE_ON {
                    route | bits
                } else {
                    route & !bits
                };
            }
            tag::RUN => self.start(spec, v, op.a as usize, op.b as i32, how.call),
            tag::BIND => {
                if how.first && op.cell == CTRL && op.written {
                    self.voices[v].gate |= 1;
                }
                self.set_mod(v, Bind::of(op), how.command, how.fresh);
            }
            _ => self.stop(spec, v, op),
        }
        Ok(())
    }

    fn instrument(&mut self, spec: &Spec, v: usize, ins: i32, ev: &Event, c: i64) -> Res<()> {
        let ins = spec.inss[ins as usize];
        if ins.ctrl >= 0 {
            self.start(spec, v, CONTROL as usize, ins.ctrl, c);
        }
        if ins.pw >= 0 && !ev.keep_pw {
            self.start(spec, v, PULSE as usize, ins.pw, c);
        }
        if ins.filter >= 0 && !ev.keep_filter {
            self.start(spec, v, FILTER as usize, ins.filter, c);
        }
        for &(slot, prog) in &spec.layer_pairs[ins.layers.0..ins.layers.0 + ins.layers.1] {
            let kept = match spec.kinds[slot] {
                PULSE => ev.keep_pw,
                FILTER => ev.keep_filter,
                _ => false,
            };
            if !kept {
                self.start(spec, v, slot, prog, c);
            }
        }
        for op in spec.ops_of(ins.binds) {
            if op.tag == tag::BIND {
                self.set_mod(v, Bind::of(op), false, true);
            } else {
                self.stop(spec, v, op);
            }
        }
        self.voices[v].freq_hold = false;
        Ok(())
    }

    fn event(&mut self, spec: &Spec, c: i64, it: Item) -> Res<()> {
        let ev = &spec.events[it.event];
        let v = it.voice;
        if it.last >= 0 {
            let how = How {
                call: c,
                first: true,
                fresh: false,
                command: false,
            };
            self.apply(spec, v, spec.rows[it.last as usize].ops, how)?;
        }
        match ev.note {
            Note::Pitch(n) => {
                self.voices[v].note = Some(n + it.transpose);
                if !ev.legato {
                    let voice = &mut self.voices[v];
                    voice.relative = 0;
                    voice.absolute = None;
                    voice.gate = 1;
                    if it.ins >= 0 {
                        self.instrument(spec, v, it.ins, ev, c)?;
                    }
                }
            }
            Note::On | Note::Off if !matches!(it.part, Part::Ops | Part::Chip) => {
                self.voices[v].gate = (ev.note == Note::On) as i64;
            }
            _ => {}
        }
        if it.part != Part::Gate {
            for op in spec.ops_of(ev.ops) {
                if it.part == Part::Whole || ((it.part == Part::Chip) == op.chip) {
                    let how = How {
                        call: c,
                        first: false,
                        fresh: false,
                        command: true,
                    };
                    self.apply_op(spec, v, op, how)?;
                }
            }
        }
        Ok(())
    }

    fn note_row(&mut self, spec: &Spec, v: usize, row: &Row, c: i64) {
        let nr = spec.row_events;
        for &e in &spec.row_event_ids[row.events.0..row.events.0 + row.events.1] {
            self.row_at[v * nr + e as usize] = c;
        }
    }

    fn run_slot(&mut self, spec: &Spec, v: usize, kind: usize, c: i64) -> Res<()> {
        let pause = spec.pauses[kind];
        if pause >= 0 && self.marked(spec, v, pause, c) {
            self.skipped.push((v, kind));
            return Ok(());
        }
        let clock = c - self.voices[v].lost[kind];
        let slot = self.voices[v].slots[kind];
        if slot.prog < 0 || slot.due > clock {
            return Ok(());
        }
        let control = spec.kinds[kind] == CONTROL;
        self.seen.clear();
        loop {
            let slot = self.voices[v].slots[kind];
            if slot.prog < 0 || slot.due > clock {
                return Ok(());
            }
            let prog = spec.progs[slot.prog as usize];
            if self.seen.contains(&(slot.prog as usize, slot.index)) {
                return Err(format!(
                    "voice {}: a program loops in no time at call {c}",
                    v + 1
                ));
            }
            self.seen.push((slot.prog as usize, slot.index));
            let row = spec.rows[prog.rows.0 + slot.index];
            let how = How {
                call: c,
                first: false,
                fresh: prog.fresh,
                command: false,
            };
            self.apply(spec, v, row.ops, how)?;
            if control {
                self.note_row(spec, v, &row, c);
            }
            let now = self.voices[v].slots[kind];
            if now.epoch != slot.epoch || now.prog < 0 {
                continue;
            }
            let mut index = slot.index + 1;
            if index == prog.rows.1 {
                if prog.loop_row < 0 {
                    self.voices[v].slots[kind].prog = NONE;
                    return Ok(());
                }
                index = prog.loop_row as usize;
            }
            let next = &mut self.voices[v].slots[kind];
            next.index = index;
            next.due = clock + row.hold + spec.rows[prog.rows.0 + index].wait;
        }
    }

    fn chain(&mut self, spec: &Spec, base: usize, cell: usize, mut value: i64) -> Res<Option<i64>> {
        let n = spec.nslots[cell];
        if !self.mods[base..base + n].iter().any(|m| m.func >= 0) {
            return Ok(None);
        }
        let c = self.call;
        let mask = MASKS[cell];
        let marks_at = self.mod_off[cell];
        for k in 0..n {
            let m = self.mods[base + k];
            if m.func < 0 {
                continue;
            }
            let (reset, hold) = spec.slot_marks[marks_at + k];
            let by = m.by as usize;
            let mut phi = m.phi;
            if reset >= 0 && self.marked(spec, by, reset, c) {
                phi = 0;
            }
            if !(hold >= 0 && self.marked(spec, by, hold, c)) {
                let voice = &mut self.voices[by];
                let note = voice.sounding().unwrap_or(0);
                let event = voice.note.unwrap_or(0);
                let out = match &spec.funcs[m.func as usize] {
                    Func::Expr(code) => {
                        expr::eval(code, &spec.tables, &mut self.stack, value, phi, note, event)
                    }
                    Func::Code(i) => {
                        let state = if song_cell(cell) {
                            &mut self.song_state[cell]
                        } else {
                            &mut voice.state[cell]
                        };
                        let inputs = [
                            value as u8,
                            (value >> 8) as u8,
                            phi as u8,
                            (phi >> 8) as u8,
                            note as u8,
                            event as u8,
                        ];
                        self.machine
                            .as_mut()
                            .ok_or("no machine for a code modulator")?
                            .fire(*i, &spec.routines[*i], inputs, state)?
                    }
                } & mask;
                if let Some(log) = self.fired.as_mut() {
                    log.push([c, cell as i64, k as i64, by as i64, phi, value, out]);
                }
                value = out;
                phi += 1;
                if m.once {
                    self.mods[base + k].func = -1;
                }
            }
            self.mods[base + k].phi = phi;
        }
        Ok(Some(value))
    }

    fn fire(&mut self, spec: &Spec, cell: usize) -> Res<()> {
        if song_cell(cell) {
            if spec.shared[cell] && self.owner[cell] < 0 {
                return Ok(());
            }
            let base = self.mod_at(0, cell, 0);
            if let Some(out) = self.chain(spec, base, cell, self.chip[cell])? {
                self.chip[cell] = out;
            }
            return Ok(());
        }
        for v in 0..self.voices.len() {
            let base = self.mod_at(v, cell, 0);
            let voice = &self.voices[v];
            let current = match cell {
                FREQ => voice.freq,
                PW => voice.pw,
                CTRL => voice.ctrl,
                AD => voice.ad,
                _ => voice.sr,
            };
            let out = self.chain(spec, base, cell, current)?;
            let voice = &mut self.voices[v];
            match (cell, out) {
                (FREQ, None) => {
                    if let (Some(n), false) = (voice.sounding(), voice.freq_hold) {
                        voice.freq = spec.tables.freq(n);
                    }
                }
                (_, None) => {}
                (FREQ, Some(x)) => voice.freq = x,
                (PW, Some(x)) => voice.pw = x,
                (CTRL, Some(x)) => voice.ctrl = x,
                (AD, Some(x)) => voice.ad = x,
                (_, Some(x)) => voice.sr = x,
            }
        }
        Ok(())
    }

    fn voice_events(
        &mut self,
        spec: &Spec,
        c: i64,
        reading: bool,
        rows: &[Look],
        events: &[Item],
    ) -> Res<()> {
        for v in 0..self.voices.len() {
            for look in rows.iter().filter(|l| l.voice == v) {
                if reading == (spec.apart && look.first) {
                    let row = spec.rows[look.row];
                    let how = How {
                        call: c,
                        first: true,
                        fresh: false,
                        command: false,
                    };
                    self.apply(spec, v, row.ops, how)?;
                    if look.extra >= 0 {
                        self.apply(spec, v, spec.events[look.extra as usize].envelope, how)?;
                    }
                }
            }
            if !reading {
                for it in events.iter().filter(|i| i.voice == v) {
                    self.event(spec, c, *it)?;
                }
            }
        }
        Ok(())
    }

    /// Runs one call; false where the song has ended.
    fn call(&mut self, spec: &Spec) -> Res<bool> {
        let c = self.call;
        self.advance(spec)?;
        if self.finished(spec) {
            return Ok(false);
        }
        let full = spec.is_full(c);
        let slot = self.ring_at(c);
        let (rows, events) = if full {
            (
                std::mem::take(&mut self.looks[slot]),
                std::mem::take(&mut self.events[slot]),
            )
        } else {
            (Vec::new(), Vec::new())
        };
        let mut result = Ok(());
        if c == self.first {
            for k in 0..self.init.len() {
                let it = self.init[k];
                result = result.and_then(|_| self.event(spec, c, it));
            }
        }
        for phase in &spec.phases {
            if result.is_err() {
                break;
            }
            result = match *phase {
                Phase::Fire(cell) => self.fire(spec, cell),
                Phase::Slots(kind) => {
                    let layers = &spec.layers[kind as usize];
                    let mut r = Ok(());
                    for &slot in layers {
                        for v in 0..self.voices.len() {
                            r = r.and_then(|_| self.run_slot(spec, v, slot, c));
                        }
                    }
                    r
                }
                Phase::Events { reading } if full => {
                    self.voice_events(spec, c, reading, &rows, &events)
                }
                Phase::Events { .. } => Ok(()),
            };
        }
        let mut rows = rows;
        let mut events = events;
        rows.clear();
        events.clear();
        self.looks[slot] = rows;
        self.events[slot] = events;
        result?;
        self.settle(spec);
        let bit = slot;
        for word in self.held.chunks_exact_mut(self.words) {
            word[bit / 64] &= !(1 << (bit % 64));
        }
        for &(v, kind) in &self.skipped {
            self.voices[v].lost[kind] += 1;
        }
        self.skipped.clear();
        self.call += 1;
        self.stick += full as i64;
        if !self.delay.is_empty() {
            let mut regs = spec.regs;
            self.registers(&mut regs);
            self.delay.push_back(regs);
            self.regs = self.delay.pop_front().unwrap_or(spec.regs);
        }
        Ok(true)
    }

    fn settle(&mut self, spec: &Spec) {
        for (segs, voice) in self.segments.iter_mut().zip(self.voices.iter_mut()) {
            let env = std::mem::replace(&mut voice.env, NONE);
            let list = if env >= 0 {
                &spec.envs[env as usize][..]
            } else {
                &[]
            };
            if list.is_empty() {
                segs.push([voice.sr, voice.ad, voice.control()]);
            }
            for &[sr, ad, ctrl] in list {
                if sr >= 0 {
                    voice.sr = sr;
                }
                if ad >= 0 {
                    voice.ad = ad;
                }
                if ctrl >= 0 {
                    voice.ctrl = ctrl;
                    voice.gate = ctrl & 1;
                }
                segs.push([voice.sr, voice.ad, voice.control()]);
            }
        }
    }

    fn registers(&self, regs: &mut [u8; REGISTERS]) {
        for (v, voice) in self.voices.iter().enumerate() {
            let at = 7 * v;
            regs[at] = voice.freq as u8;
            regs[at + 1] = (voice.freq >> 8) as u8;
            regs[at + 2] = voice.pw as u8;
            regs[at + 3] = (voice.pw >> 8) as u8;
            regs[at + 4] = voice.control() as u8;
            regs[at + 5] = voice.ad as u8;
            regs[at + 6] = voice.sr as u8;
        }
        regs[21] = (self.chip[CUT] & 7) as u8;
        regs[22] = (self.chip[CUT] >> 3) as u8;
        regs[23] = (self.chip[RES] << 4 | self.chip[ROUTE]) as u8;
        regs[24] = (self.chip[MODE] | self.chip[VOL]) as u8;
    }

    /// Plays one frame: the registers after its last call, those computed `lag` calls before,
    /// or `None` once the song has ended.
    pub fn step(&mut self, spec: &Spec) -> Res<Option<[u8; REGISTERS]>> {
        for segs in &mut self.segments {
            segs.clear();
        }
        for _ in 0..spec.frame {
            if !self.call(spec)? {
                return Ok(None);
            }
        }
        self.tick += 1;
        if spec.lag == 0 {
            let mut regs = self.regs;
            self.registers(&mut regs);
            self.regs = regs;
        }
        Ok(Some(self.regs))
    }
}
