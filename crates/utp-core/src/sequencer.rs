//! The sequencer: a song's timeline walked to the tick each step begins.

use crate::spec::{Entry, Spec};

/// A step beginning at `tick` on `voice`: `event` an index into the events or `NO_EVENT` for a
/// step an event's `*k` times, `last` for a phrase's last step.
#[derive(Clone, Copy, Debug)]
pub struct Step {
    pub tick: i64,
    pub voice: usize,
    pub event: Option<usize>,
    pub transpose: i64,
    pub last: bool,
}

pub struct Sequencer {
    voices: usize,
    tempo: [(i64, i64); 3],
    phase: [usize; 3],
    phrase: [Option<usize>; 3],
    pos: [usize; 3],
    transpose: [i64; 3],
    rest: [i64; 3],
    next: [i64; 3],
    stopped: [bool; 3],
    row: usize,
    entry: [usize; 3],
    pub end: Option<i64>,
}

fn pick(pair: (i64, i64), phase: usize) -> i64 {
    if phase == 0 {
        pair.0
    } else {
        pair.1
    }
}

impl Sequencer {
    pub fn new(spec: &Spec) -> Sequencer {
        let mut seq = Sequencer {
            voices: spec.voices,
            tempo: [spec.tempo; 3],
            phase: [0; 3],
            phrase: [None; 3],
            pos: [0; 3],
            transpose: [0; 3],
            rest: [0; 3],
            next: [0; 3],
            stopped: [false; 3],
            row: 0,
            entry: [0; 3],
            end: None,
        };
        for (v, &e) in spec.init.iter().enumerate() {
            seq.retime(spec, v, e);
        }
        seq
    }

    fn retime(&mut self, spec: &Spec, v: usize, event: usize) {
        let event = &spec.events[event];
        if let Some(t) = event.tempo {
            self.tempo = [t; 3];
            self.phase = [0; 3];
        }
        if let Some(t) = event.vtempo {
            self.tempo[v] = t;
            self.phase[v] = 0;
        }
    }

    fn len(&self, spec: &Spec, v: usize) -> usize {
        self.phrase[v].map_or(0, |p| spec.phrases[p].len())
    }

    fn enter(&mut self, v: usize, entry: Entry) {
        if let Entry::Phrase(p, t) = entry {
            self.phrase[v] = Some(p);
            self.pos[v] = 0;
            self.transpose[v] = t;
        }
    }

    fn start_row(&mut self, spec: &Spec, t: i64, ending: u8) -> Result<(), String> {
        if let Some(orders) = &spec.orders {
            for v in (0..self.voices).filter(|&v| ending >> v & 1 != 0) {
                let (entries, loop_entry) = &orders[v];
                if self.entry[v] == entries.len() {
                    match loop_entry {
                        None => {
                            self.stopped[v] = true;
                            continue;
                        }
                        Some(l) => self.entry[v] = *l,
                    }
                }
                self.enter(v, entries[self.entry[v]]);
                self.entry[v] += 1;
            }
            if self.stopped[..self.voices].iter().all(|&s| s) {
                self.end = Some(t);
            }
            return Ok(());
        }
        if self.row == spec.timeline.len() {
            match spec.timeline_loop {
                None => {
                    self.end = Some(t);
                    return Ok(());
                }
                Some(l) => self.row = l,
            }
        }
        let index = self.row;
        self.row += 1;
        for (v, &entry) in spec.timeline[index].iter().enumerate() {
            if matches!(entry, Entry::Cont) == (ending >> v & 1 != 0) {
                return Err(format!("row {index}: voice {} at tick {t}", v + 1));
            }
            if matches!(entry, Entry::End) {
                self.stopped[v] = true;
            }
            self.enter(v, entry);
        }
        if self.stopped[..self.voices].iter().all(|&s| s) {
            self.end = Some(t);
        }
        Ok(())
    }

    /// Appends to `out` the steps that begin by tick `limit`.
    pub fn advance(&mut self, spec: &Spec, limit: i64, out: &mut Vec<Step>) -> Result<(), String> {
        while self.end.is_none() {
            let mut t = i64::MAX;
            for v in 0..self.voices {
                if !self.stopped[v] {
                    t = t.min(self.next[v]);
                }
            }
            if t > limit {
                break;
            }
            let mut due = 0u8;
            for v in 0..self.voices {
                if !self.stopped[v] && self.next[v] == t {
                    due |= 1 << v;
                }
            }
            let mut ending = 0u8;
            for v in (0..self.voices).filter(|&v| due >> v & 1 != 0) {
                if self.rest[v] == 0 && self.pos[v] == self.len(spec, v) {
                    ending |= 1 << v;
                }
            }
            if ending != 0 {
                self.start_row(spec, t, ending)?;
                if self.end.is_some() {
                    break;
                }
                for v in 0..self.voices {
                    if self.stopped[v] {
                        due &= !(1 << v);
                    }
                }
            }
            for v in (0..self.voices).filter(|&v| due >> v & 1 != 0) {
                let mut event = None;
                if self.rest[v] > 0 {
                    self.rest[v] -= 1;
                } else {
                    let id =
                        spec.phrases[self.phrase[v].ok_or("a voice has no phrase")?][self.pos[v]];
                    self.pos[v] += 1;
                    self.rest[v] = spec.events[id].steps - 1;
                    self.retime(spec, v, id);
                    event = Some(id);
                }
                let last = self.rest[v] == 0 && self.pos[v] == self.len(spec, v);
                out.push(Step {
                    tick: t,
                    voice: v,
                    event,
                    transpose: self.transpose[v],
                    last,
                });
            }
            for v in (0..self.voices).filter(|&v| due >> v & 1 != 0) {
                self.next[v] = t + pick(self.tempo[v], self.phase[v]);
                self.phase[v] ^= 1;
            }
        }
        Ok(())
    }
}
