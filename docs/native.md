# The native call machine

`unitracker_player._core` is a Rust implementation of the call machine of `player.py`
(`crates/utp-core`, bound to Python by `crates/utp-py`). It plays the Song that
`compiler.py` compiles and produces the register rows `Player.step` produces, call for call;
the Python `Player` stays as the oracle, the machine that `observe` hooks into, and the player
where the extension is absent.

`unitracker_player.player.play` uses the native machine when it is built, the environment
variable `UNITRACKER_PLAYER_NATIVE` is not `0` and the Song has a native form, and the Python
machine otherwise. A native error (a code modulator past its budget, a loop in no time, a row
that contradicts its phrases) falls back to the Python machine, which raises its own `PlayError`.

## The stream

`unitracker_player.native.export(song)` writes the Song as one flat list of integers and the
6502 memory image of its code modulators:

| section | contents |
| --- | --- |
| head | magic, voices, calls per frame, lag, the `full` pattern, `regs`, horizon, tempo, phases, kinds and layers of the program slots, slots, shared cells and state sizes per cell |
| tables | `F(k)` for k -1024 to 1023, the named tables |
| marks | the events slots hold, reset and pause on, their row events, each slot's reset and hold mark |
| modulators | a postfix code for each expression, the routine index for each code modulator |
| routines | `start`, `end` and dirty ranges of each routine of the image |
| arenas | envs, ops, rows, programs, layers, instruments, events, phrases |
| structure | the timeline's rows or the voices' orderlists, the init events, the default bindings |

Objects the Python compiler shares (programs, instruments, events, phrases, modulators) are
exported once and addressed by index, so the Rust identity tests (`mod[0] is fn`) are index
comparisons. Rows and ops are contiguous in their arenas.

## The machine

* Expressions are postfix code over `v`, `phi`, `N`, `n` evaluated on 64-bit wrapping
  integers with Python's floor division and modulo; a division by 0 and a shift by a negative
  count are 0.
* Code modulators run on `jennings-core` directly: one 64 KiB memory per song, each firing
  loaded from the image and restored over the routine's dirty ranges, stopping at the top-level
  `rts`.
* Events, lookahead rows and the held-call sets are rings of calls indexed by call number; a call
  past the ring's span, or a stream the reader finds malformed, is a native error.
* No allocation happens after construction: events, steps and segments reuse their buffers.

## Checking

`tools/native_diff.py DOC...` plays documents with both machines and prints the first differing
`(frame, register)`. The test suite plays every document it plays through both machines
(`tests/conftest.py`, the `agree` fixture) and `tests/test_native.py` adds random expressions,
the checked-in songs, the writes of each call and the code modulator machine against the Python
routine.

## Building

`maturin develop --release` builds the extension in place; `cargo fmt --all --check`,
`cargo clippy --workspace --all-targets -- -D warnings` and `cargo test --workspace` check the
crates. CI builds the wheel in `docker/native.Dockerfile` and runs the suite against it; the
wheels and sdist are published by `.github/workflows/publish.yml`.
