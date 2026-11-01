//! `unitracker_player._core`: the native call machine for Python.

use pyo3::create_exception;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use utp_core::spec::REGISTERS;
use utp_core::{Machine, Player, Spec};

create_exception!(
    _core,
    NativeError,
    PyException,
    "A document the native player does not play."
);

fn fail(message: String) -> PyErr {
    NativeError::new_err(message)
}

/// A compiled document and the state of its play.
#[pyclass]
struct NativePlayer {
    spec: Spec,
    player: Player,
}

#[pymethods]
impl NativePlayer {
    /// `spec` is the flat stream of `unitracker_player.native.export`, `image` the 6502
    /// memory of the document's code modulators, empty where it has none.
    #[new]
    #[pyo3(signature = (spec, image=None))]
    fn new(spec: Vec<i64>, image: Option<&[u8]>) -> PyResult<Self> {
        let spec = Spec::parse(spec).map_err(fail)?;
        let machine = match image {
            Some(bytes) if !bytes.is_empty() => {
                Some(Machine::new(bytes, &spec.routines).map_err(fail)?)
            }
            _ => None,
        };
        let player = Player::new(&spec, machine).map_err(fail)?;
        Ok(NativePlayer { spec, player })
    }

    /// Whether the song plays forever.
    #[getter]
    fn looping(&self) -> bool {
        self.spec.is_looping()
    }

    /// The frames played.
    #[getter]
    fn tick(&self) -> i64 {
        self.player.tick
    }

    /// The registers after the next frame, or None once the song has ended.
    fn step<'py>(&mut self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyBytes>>> {
        let regs = self.player.step(&self.spec).map_err(fail)?;
        Ok(regs.map(|r| PyBytes::new(py, &r)))
    }

    /// The registers after each of up to `frames` frames, 25 bytes each, fewer where the song
    /// ends first.
    fn run<'py>(&mut self, py: Python<'py>, frames: usize) -> PyResult<Bound<'py, PyBytes>> {
        let (spec, player) = (&self.spec, &mut self.player);
        let out = py
            .detach(|| {
                let mut out = Vec::with_capacity(frames * REGISTERS);
                for _ in 0..frames {
                    match player.step(spec)? {
                        Some(regs) => out.extend_from_slice(&regs),
                        None => break,
                    }
                }
                Ok(out)
            })
            .map_err(fail)?;
        Ok(PyBytes::new(py, &out))
    }

    /// Starts recording the firings.
    fn trace(&mut self) {
        self.player.fired = Some(Vec::new());
    }

    /// The firings recorded, as `(call, cell, slot, voice, phi, value, out)`, and forgets them.
    fn fired(&mut self) -> Vec<(i64, i64, i64, i64, i64, i64, i64)> {
        let log = self
            .player
            .fired
            .as_mut()
            .map(std::mem::take)
            .unwrap_or_default();
        log.iter()
            .map(|f| (f[0], f[1], f[2], f[3], f[4], f[5], f[6]))
            .collect()
    }

    /// Fires code modulator routine `index` on `(v, phi, N, n)` with `state`: the `v` it
    /// returns and the state it leaves.
    fn fire(
        &mut self,
        index: usize,
        v: i64,
        phi: i64,
        note: i64,
        event: i64,
        state: Vec<u8>,
    ) -> PyResult<(i64, Vec<u8>)> {
        let inputs = [
            v as u8,
            (v >> 8) as u8,
            phi as u8,
            (phi >> 8) as u8,
            note as u8,
            event as u8,
        ];
        let mut state = state;
        let out = self
            .player
            .fire_routine(&self.spec, index, inputs, &mut state)
            .map_err(fail)?;
        Ok((out, state))
    }

    /// Per voice the `(sr, ad, ctrl)` writes of each call of the last frame.
    fn segments(&self) -> Vec<Vec<(i64, i64, i64)>> {
        self.player
            .segments
            .iter()
            .map(|v| v.iter().map(|s| (s[0], s[1], s[2])).collect())
            .collect()
    }
}

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<NativePlayer>()?;
    m.add("NativeError", m.py().get_type::<NativeError>())?;
    Ok(())
}
