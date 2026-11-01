//! The compiled document as the flat stream of integers the Python front end writes, read into
//! arenas of plain records addressed by index.

use crate::expr::{self, Tables};

pub const CELLS: usize = 10;
pub const FREQ: usize = 0;
pub const PW: usize = 1;
pub const CUT: usize = 2;
pub const CTRL: usize = 3;
pub const AD: usize = 4;
pub const SR: usize = 5;
pub const RES: usize = 6;
pub const ROUTE: usize = 7;
pub const MODE: usize = 8;
pub const VOL: usize = 9;
pub const MASKS: [i64; CELLS] = [65535, 4095, 2047, 255, 255, 255, 15, 15, 240, 15];
pub const MAGIC: i64 = 0x5554_5031;
pub const NONE: i32 = -1;
pub const CONTROL: u8 = 0;
pub const PULSE: u8 = 1;
pub const FILTER: u8 = 2;
pub const KINDS: usize = 3;
pub const REGISTERS: usize = 25;

#[inline]
pub fn song_cell(cell: usize) -> bool {
    matches!(cell, CUT | RES | ROUTE | MODE | VOL)
}

pub mod tag {
    pub const ENV: i64 = 0;
    pub const MASK: i64 = 1;
    pub const GATE: i64 = 2;
    pub const NOTE_REL: i64 = 3;
    pub const NOTE_ABS: i64 = 4;
    pub const ROUTE_ON: i64 = 5;
    pub const ROUTE_OFF: i64 = 6;
    pub const RUN: i64 = 7;
    pub const BIND: i64 = 8;
    pub const RESET: i64 = 9;
    pub const CLEAR: i64 = 10;
    pub const HOLD: i64 = 11;
}

/// What a phase of a call does.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    Fire(usize),
    Slots(u8),
    Events { reading: bool },
}

/// A modulator: a postfix expression or a routine of the 6502 machine.
pub enum Func {
    Expr(Vec<i64>),
    Code(usize),
}

/// One operation of a row or an event; `a`, `b`, `c`, `d`, `e` depend on the tag.
#[derive(Clone, Copy, Debug)]
pub struct Op {
    pub tag: i64,
    pub cell: usize,
    pub a: i64,
    pub b: i64,
    pub once: bool,
    pub written: bool,
    pub chip: bool,
}

#[derive(Clone, Copy, Debug)]
pub struct Row {
    pub wait: i64,
    pub hold: i64,
    pub ops: (usize, usize),
    pub events: (usize, usize),
    pub before: i64,
}

#[derive(Clone, Copy, Debug)]
pub struct Prog {
    pub rows: (usize, usize),
    pub loop_row: i32,
    pub fresh: bool,
}

#[derive(Clone, Copy, Debug)]
pub struct Ins {
    pub ctrl: i32,
    pub pw: i32,
    pub filter: i32,
    pub binds: (usize, usize),
    pub restart: (usize, usize),
    pub layers: (usize, usize),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Note {
    Rest,
    Pitch(i64),
    On,
    Off,
}

#[derive(Clone, Copy, Debug)]
pub struct Event {
    pub note: Note,
    pub ins: i32,
    pub ops: (usize, usize),
    pub keep_pw: bool,
    pub keep_filter: bool,
    pub steps: i64,
    pub legato: bool,
    pub tempo: Option<(i64, i64)>,
    pub vtempo: Option<(i64, i64)>,
    pub envelope: (usize, usize),
    pub any_chip: bool,
    pub all_chip: bool,
    pub run_program: bool,
}

#[derive(Clone, Copy, Debug)]
pub enum Entry {
    Cont,
    End,
    Phrase(usize, i64),
}

pub struct Routine {
    pub start: u16,
    pub end: u16,
    pub dirty: Vec<(usize, usize)>,
}

pub struct Mark {
    pub events: Vec<(u8, i64)>,
    pub rows: Vec<u32>,
}

pub struct Spec {
    pub voices: usize,
    pub frame: usize,
    pub lag: usize,
    pub full: Vec<bool>,
    pub ones: Vec<usize>,
    pub regs: [u8; REGISTERS],
    pub horizon: i64,
    pub tempo: (i64, i64),
    pub phases: Vec<Phase>,
    pub apart: bool,
    pub kinds: Vec<u8>,
    pub layers: [Vec<usize>; KINDS],
    pub nslots: [usize; CELLS],
    pub shared: [bool; CELLS],
    pub state: [usize; CELLS],
    pub tables: Tables,
    pub marks: Vec<Mark>,
    pub row_events: usize,
    pub slot_marks: Vec<(i32, i32)>,
    pub pauses: Vec<i32>,
    pub funcs: Vec<Func>,
    pub depth: usize,
    pub routines: Vec<Routine>,
    pub envs: Vec<Vec<[i64; 3]>>,
    pub ops: Vec<Op>,
    pub row_event_ids: Vec<u32>,
    pub rows: Vec<Row>,
    pub progs: Vec<Prog>,
    pub layer_pairs: Vec<(usize, i32)>,
    pub inss: Vec<Ins>,
    pub events: Vec<Event>,
    pub phrases: Vec<Vec<usize>>,
    pub timeline: Vec<Vec<Entry>>,
    pub orders: Option<Vec<(Vec<Entry>, Option<usize>)>>,
    pub timeline_loop: Option<usize>,
    pub init: Vec<usize>,
    pub defaults: Vec<(usize, usize, i32, bool)>,
}

struct Reader {
    data: Vec<i64>,
    at: usize,
}

impl Reader {
    fn int(&mut self) -> Result<i64, String> {
        let x = *self.data.get(self.at).ok_or("spec ends early")?;
        self.at += 1;
        Ok(x)
    }

    fn size(&mut self) -> Result<usize, String> {
        let x = self.int()?;
        usize::try_from(x).map_err(|_| format!("spec: bad count {x}"))
    }

    fn flag(&mut self) -> Result<bool, String> {
        Ok(self.int()? != 0)
    }

    fn index(&mut self) -> Result<i32, String> {
        let x = self.int()?;
        i32::try_from(x).map_err(|_| format!("spec: bad index {x}"))
    }

    fn ints(&mut self, n: usize) -> Result<Vec<i64>, String> {
        let end = self.at.checked_add(n).filter(|&e| e <= self.data.len());
        let end = end.ok_or("spec ends early")?;
        let out = self.data[self.at..end].to_vec();
        self.at = end;
        Ok(out)
    }

    fn span(&mut self) -> Result<(usize, usize), String> {
        Ok((self.size()?, self.size()?))
    }

    fn pair(&mut self) -> Result<Option<(i64, i64)>, String> {
        let (a, b) = (self.int()?, self.int()?);
        Ok((a >= 0).then_some((a, b)))
    }
}

fn within(span: (usize, usize), len: usize, what: &str) -> Result<(), String> {
    if span.0 + span.1 <= len {
        Ok(())
    } else {
        Err(format!("spec: {what} past its arena"))
    }
}

fn entries(r: &mut Reader, phrases: usize) -> Result<Vec<Entry>, String> {
    let n = r.size()?;
    (0..n)
        .map(|_| {
            let (kind, phrase, transpose) = (r.int()?, r.size()?, r.int()?);
            match kind {
                0 => Ok(Entry::Cont),
                1 => Ok(Entry::End),
                2 if phrase < phrases => Ok(Entry::Phrase(phrase, transpose)),
                _ => Err("spec: bad entry".to_string()),
            }
        })
        .collect()
}

impl Spec {
    /// Reads the stream; `Err` names what is malformed.
    pub fn parse(data: Vec<i64>) -> Result<Spec, String> {
        let mut r = Reader { data, at: 0 };
        if r.int()? != MAGIC {
            return Err("spec: bad magic".into());
        }
        let voices = r.size()?;
        if !(1..=3).contains(&voices) {
            return Err("spec: voices".into());
        }
        let (frame, lag) = (r.size()?, r.size()?);
        let nfull = r.size()?;
        let full: Vec<bool> = r.ints(nfull)?.iter().map(|&x| x != 0).collect();
        let ones: Vec<usize> = (0..full.len()).filter(|&i| full[i]).collect();
        if !full.is_empty() && ones.is_empty() {
            return Err("spec: no full call".into());
        }
        let mut regs = [0u8; REGISTERS];
        for (slot, x) in regs.iter_mut().zip(r.ints(REGISTERS)?) {
            *slot = x as u8;
        }
        let horizon = r.int()?;
        let tempo = (r.int()?, r.int()?);
        let phases = (0..r.size()?)
            .map(|_| {
                let (kind, arg) = (r.int()?, r.size()?);
                match kind {
                    0 if arg < CELLS => Ok(Phase::Fire(arg)),
                    1 if arg < KINDS => Ok(Phase::Slots(arg as u8)),
                    2 => Ok(Phase::Events { reading: arg != 0 }),
                    _ => Err("spec: bad phase".to_string()),
                }
            })
            .collect::<Result<Vec<_>, _>>()?;
        let apart = r.flag()?;
        let nkinds = r.size()?;
        let kinds: Vec<u8> = r.ints(nkinds)?.iter().map(|&x| x as u8).collect();
        let mut layers: [Vec<usize>; KINDS] = Default::default();
        for layer in layers.iter_mut() {
            let n = r.size()?;
            *layer = r.ints(n)?.iter().map(|&x| x as usize).collect();
            if layer.iter().any(|&s| s >= nkinds) {
                return Err("spec: layer slot".into());
            }
        }
        let mut nslots = [0usize; CELLS];
        let mut shared = [false; CELLS];
        let mut state = [0usize; CELLS];
        for x in nslots.iter_mut() {
            *x = r.size()?;
        }
        for x in shared.iter_mut() {
            *x = r.flag()?;
        }
        for x in state.iter_mut() {
            *x = r.size()?;
        }
        let f_low = r.int()?;
        let nf = r.size()?;
        let f = r.ints(nf)?;
        let named = (0..r.size()?)
            .map(|_| {
                let n = r.size()?;
                r.ints(n)
            })
            .collect::<Result<Vec<_>, _>>()?;
        let tables = Tables { f_low, f, named };
        let nmarks = r.size()?;
        let mut marks = Vec::with_capacity(nmarks);
        for _ in 0..nmarks {
            let events = (0..r.size()?)
                .map(|_| Ok((r.int()? as u8, r.int()?)))
                .collect::<Result<Vec<_>, String>>()?;
            let rows = (0..r.size()?)
                .map(|_| Ok(r.size()? as u32))
                .collect::<Result<Vec<_>, String>>()?;
            marks.push(Mark { events, rows });
        }
        let row_events = r.size()?;
        if marks
            .iter()
            .any(|m| m.rows.iter().any(|&e| e as usize >= row_events))
        {
            return Err("spec: row event".into());
        }
        let total: usize = nslots.iter().sum();
        let mut slot_marks = Vec::with_capacity(total);
        for _ in 0..total {
            let (reset, hold) = (r.index()?, r.index()?);
            for m in [reset, hold] {
                if m >= nmarks as i32 {
                    return Err("spec: slot mark".into());
                }
            }
            slot_marks.push((reset, hold));
        }
        let pauses = (0..nkinds)
            .map(|_| r.index())
            .collect::<Result<Vec<_>, _>>()?;
        if pauses.iter().any(|&m| m >= nmarks as i32) {
            return Err("spec: pause mark".into());
        }
        let nfuncs = r.size()?;
        let mut funcs = Vec::with_capacity(nfuncs);
        let mut depth = 1;
        for _ in 0..nfuncs {
            match r.int()? {
                0 => {
                    let n = r.size()?;
                    let code = r.ints(n)?;
                    depth = depth.max(expr::check(&code, tables.named.len())?);
                    funcs.push(Func::Expr(code));
                }
                1 => funcs.push(Func::Code(r.size()?)),
                _ => return Err("spec: bad function".into()),
            }
        }
        let mut routines = Vec::new();
        for _ in 0..r.size()? {
            let (start, end) = (r.size()?, r.size()?);
            let dirty = (0..r.size()?)
                .map(|_| r.span())
                .collect::<Result<Vec<_>, _>>()?;
            if start > 0xFFFF || end > 0x10000 || start >= end {
                return Err("spec: routine".into());
            }
            routines.push(Routine {
                start: start as u16,
                end: end.min(0xFFFF) as u16,
                dirty,
            });
        }
        if funcs
            .iter()
            .any(|f| matches!(f, Func::Code(i) if *i >= routines.len()))
        {
            return Err("spec: routine index".into());
        }
        let mut envs = Vec::new();
        for _ in 0..r.size()? {
            let n = r.size()?;
            let mut segs = Vec::with_capacity(n);
            for _ in 0..n {
                segs.push([r.int()?, r.int()?, r.int()?]);
            }
            envs.push(segs);
        }
        let nops = r.size()?;
        let mut ops = Vec::with_capacity(nops);
        for _ in 0..nops {
            let (tag, cell) = (r.int()?, r.size()?);
            let (a, b) = (r.int()?, r.int()?);
            let (once, written, chip) = (r.flag()?, r.flag()?, r.flag()?);
            if cell >= CELLS || !(0..=11).contains(&tag) {
                return Err("spec: bad op".into());
            }
            let slot_ok = match tag {
                tag::BIND => (a as usize) < nslots[cell] && (b as usize) < funcs.len(),
                tag::RESET | tag::CLEAR | tag::HOLD => a >= 0 && a < (1 << nslots[cell]),
                tag::ENV => (a as usize) < envs.len(),
                tag::RUN => (a as usize) < nkinds,
                _ => true,
            };
            if !slot_ok {
                return Err("spec: op operand".into());
            }
            ops.push(Op {
                tag,
                cell,
                a,
                b,
                once,
                written,
                chip,
            });
        }
        let row_event_ids = (0..r.size()?)
            .map(|_| Ok(r.size()? as u32))
            .collect::<Result<Vec<_>, String>>()?;
        if row_event_ids.iter().any(|&e| e as usize >= row_events) {
            return Err("spec: row event id".into());
        }
        let nrows = r.size()?;
        let mut rows = Vec::with_capacity(nrows);
        for _ in 0..nrows {
            let (wait, hold) = (r.int()?, r.int()?);
            let (ops_span, ev_span, before) = (r.span()?, r.span()?, r.int()?);
            within(ops_span, ops.len(), "row ops")?;
            within(ev_span, row_event_ids.len(), "row events")?;
            rows.push(Row {
                wait,
                hold,
                ops: ops_span,
                events: ev_span,
                before,
            });
        }
        let nprogs = r.size()?;
        let mut progs = Vec::with_capacity(nprogs);
        for _ in 0..nprogs {
            let span = r.span()?;
            let (loop_row, fresh) = (r.index()?, r.flag()?);
            within(span, rows.len(), "program rows")?;
            if loop_row >= span.1 as i32 {
                return Err("spec: loop row".into());
            }
            progs.push(Prog {
                rows: span,
                loop_row,
                fresh,
            });
        }
        if ops
            .iter()
            .any(|o| o.tag == tag::RUN && o.b >= progs.len() as i64)
        {
            return Err("spec: run program".into());
        }
        let layer_pairs = (0..r.size()?)
            .map(|_| Ok((r.size()?, r.index()?)))
            .collect::<Result<Vec<_>, String>>()?;
        let prog_ok = |p: i32| p < progs.len() as i32;
        if layer_pairs.iter().any(|&(s, p)| s >= nkinds || !prog_ok(p)) {
            return Err("spec: layer".into());
        }
        let ninss = r.size()?;
        let mut inss = Vec::with_capacity(ninss);
        for _ in 0..ninss {
            let (ctrl, pw, filter) = (r.index()?, r.index()?, r.index()?);
            let (binds, restart, layers_span) = (r.span()?, r.span()?, r.span()?);
            within(binds, ops.len(), "binds")?;
            within(restart, rows.len(), "restart")?;
            within(layers_span, layer_pairs.len(), "layers")?;
            if ![ctrl, pw, filter].into_iter().all(prog_ok) {
                return Err("spec: instrument program".into());
            }
            inss.push(Ins {
                ctrl,
                pw,
                filter,
                binds,
                restart,
                layers: layers_span,
            });
        }
        let nevents = r.size()?;
        let mut events = Vec::with_capacity(nevents);
        for _ in 0..nevents {
            let (kind, value, ins) = (r.int()?, r.int()?, r.index()?);
            let ops_span = r.span()?;
            let (keep_pw, keep_filter, steps, legato) = (r.flag()?, r.flag()?, r.int()?, r.flag()?);
            let (tempo, vtempo, envelope) = (r.pair()?, r.pair()?, r.span()?);
            within(ops_span, ops.len(), "event ops")?;
            within(envelope, ops.len(), "event envelope")?;
            if ins >= inss.len() as i32 || steps < 1 {
                return Err("spec: event".into());
            }
            let note = match kind {
                0 => Note::Rest,
                1 => Note::Pitch(value),
                2 => Note::On,
                3 => Note::Off,
                _ => return Err("spec: note".into()),
            };
            let list = &ops[ops_span.0..ops_span.0 + ops_span.1];
            let run_program = list.iter().any(|o| o.tag == tag::RUN && o.b >= 0);
            events.push(Event {
                note,
                ins,
                ops: ops_span,
                keep_pw,
                keep_filter,
                steps,
                legato,
                tempo,
                vtempo,
                envelope,
                any_chip: list.iter().any(|o| o.chip),
                all_chip: list.iter().all(|o| o.chip),
                run_program,
            });
        }
        let phrases = (0..r.size()?)
            .map(|_| {
                let n = r.size()?;
                let ids: Vec<usize> = r.ints(n)?.iter().map(|&x| x as usize).collect();
                if ids.iter().any(|&e| e >= nevents) {
                    return Err("spec: phrase event".to_string());
                }
                Ok(ids)
            })
            .collect::<Result<Vec<_>, _>>()?;
        let mode = r.int()?;
        let (mut timeline, mut orders, mut timeline_loop) = (Vec::new(), None, None);
        if mode == 0 {
            for _ in 0..r.size()? {
                let row = entries(&mut r, phrases.len())?;
                if row.len() != voices {
                    return Err("spec: timeline row".into());
                }
                timeline.push(row);
            }
            timeline_loop = usize::try_from(r.int()?).ok();
        } else {
            let mut per = Vec::new();
            for _ in 0..voices {
                let list = entries(&mut r, phrases.len())?;
                per.push((list, usize::try_from(r.int()?).ok()));
            }
            orders = Some(per);
        }
        let init = (0..r.size()?)
            .map(|_| r.size())
            .collect::<Result<Vec<_>, _>>()?;
        if init.iter().any(|&e| e >= nevents) {
            return Err("spec: init event".into());
        }
        let defaults = (0..r.size()?)
            .map(|_| {
                let (cell, slot, f, once) = (r.size()?, r.size()?, r.index()?, r.flag()?);
                if cell >= CELLS || slot >= nslots[cell] || f < 0 || f as usize >= funcs.len() {
                    return Err("spec: default".to_string());
                }
                Ok((cell, slot, f, once))
            })
            .collect::<Result<Vec<_>, _>>()?;
        if r.at != r.data.len() {
            return Err("spec: trailing data".into());
        }
        Ok(Spec {
            voices,
            frame,
            lag,
            full,
            ones,
            regs,
            horizon,
            tempo,
            phases,
            apart,
            kinds,
            layers,
            nslots,
            shared,
            state,
            tables,
            marks,
            row_events,
            slot_marks,
            pauses,
            funcs,
            depth,
            routines,
            envs,
            ops,
            row_event_ids,
            rows,
            progs,
            layer_pairs,
            inss,
            events,
            phrases,
            timeline,
            orders,
            timeline_loop,
            init,
            defaults,
        })
    }

    /// The call that is sequencer tick `tick`, the `tick`th full call.
    #[inline]
    pub fn call_of(&self, tick: i64) -> i64 {
        if self.full.is_empty() {
            return tick;
        }
        let n = self.ones.len() as i64;
        tick.div_euclid(n) * self.full.len() as i64 + self.ones[tick.rem_euclid(n) as usize] as i64
    }

    #[inline]
    pub fn is_full(&self, call: i64) -> bool {
        self.full.is_empty() || self.full[(call % self.full.len() as i64) as usize]
    }

    pub fn ops_of(&self, span: (usize, usize)) -> &[Op] {
        &self.ops[span.0..span.0 + span.1]
    }

    pub fn rows_of(&self, span: (usize, usize)) -> &[Row] {
        &self.rows[span.0..span.0 + span.1]
    }

    pub fn is_looping(&self) -> bool {
        match &self.orders {
            Some(o) => o.iter().any(|(_, l)| l.is_some()),
            None => self.timeline_loop.is_some(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_stream_without_the_magic_is_refused() {
        assert!(Spec::parse(vec![]).is_err());
        assert_eq!(
            Spec::parse(vec![1, 2]).err().as_deref(),
            Some("spec: bad magic")
        );
    }

    #[test]
    fn a_stream_that_ends_early_is_refused() {
        assert_eq!(
            Spec::parse(vec![MAGIC, 1]).err().as_deref(),
            Some("spec ends early")
        );
    }

    #[test]
    fn a_voice_count_outside_one_to_three_is_refused() {
        for voices in [0, 4] {
            assert!(Spec::parse(vec![MAGIC, voices, 1, 0]).is_err());
        }
    }
}
