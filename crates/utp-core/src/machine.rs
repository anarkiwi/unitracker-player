//! Code modulators: one 6502 memory per song, each firing run on jennings' native core from the
//! memory as the routines were assembled.

use jennings_core::{run_until, AddrSet, Cpu, Stop, Until};

use crate::spec::Routine;

pub const BUDGET: u64 = 4096;
pub const MAX_CYCLES: u64 = 8 * BUDGET;
pub const EXIT: u16 = 0xFFFF;
pub const STATE: usize = 6;
pub const STACK: usize = 0x100;
const INTERRUPT: u8 = 0x04;

pub struct Machine {
    mem: Box<[u8; 0x10000]>,
    image: Box<[u8; 0x10000]>,
    cpu: Cpu,
    stops: Vec<AddrSet>,
}

impl Machine {
    pub fn new(image: &[u8], routines: &[Routine]) -> Result<Machine, String> {
        let image: Box<[u8; 0x10000]> = image
            .to_vec()
            .into_boxed_slice()
            .try_into()
            .map_err(|_| "machine: the image is not 64 KiB".to_string())?;
        let stops = routines
            .iter()
            .map(|r| [EXIT, r.end].into_iter().collect())
            .collect();
        Ok(Machine {
            mem: image.clone(),
            image,
            cpu: Cpu::new(),
            stops,
        })
    }

    /// Runs routine `r` on `(v, phi, note, event)` with its cell's `state`, updated in place;
    /// the 16-bit `v` it leaves, or `Err` where it does not return to its caller within the
    /// budget.
    pub fn fire(
        &mut self,
        index: usize,
        r: &Routine,
        inputs: [u8; 6],
        state: &mut [u8],
    ) -> Result<i64, String> {
        let mem = &mut *self.mem;
        mem[..STATE].copy_from_slice(&inputs);
        mem[STATE..STATE + state.len()].copy_from_slice(state);
        mem[STACK] = (EXIT - 1) as u8;
        mem[STACK + 1] = ((EXIT - 1) >> 8) as u8;
        let cpu = &mut self.cpu;
        cpu.a = 0;
        cpu.x = 0;
        cpu.y = 0;
        cpu.sp = 0xFF;
        cpu.set_p(INTERRUPT);
        cpu.pc = r.start;
        let begun = cpu.steps;
        let mut until = Until {
            max_cycles: Some(MAX_CYCLES),
            stop_pcs: Some(&self.stops[index]),
            stop_on_jam: true,
            ..Until::default()
        };
        let stop = run_until(cpu, mem, &mut until, || false);
        if stop != Stop::Pc || cpu.pc != EXIT || cpu.sp != 1 || cpu.steps - begun > BUDGET {
            self.mem.copy_from_slice(&self.image[..]);
            return Err(format!(
                "a code modulator ran outside its budget: {stop:?} pc {:#06x} sp {} steps {}",
                cpu.pc,
                cpu.sp,
                cpu.steps - begun
            ));
        }
        let mem = &mut *self.mem;
        state.copy_from_slice(&mem[STATE..STATE + state.len()]);
        let out = mem[0] as i64 | (mem[1] as i64) << 8;
        mem[STACK..STACK + 2].copy_from_slice(&self.image[STACK..STACK + 2]);
        for &(lo, hi) in &r.dirty {
            mem[lo..hi].copy_from_slice(&self.image[lo..hi]);
        }
        Ok(out)
    }
}
