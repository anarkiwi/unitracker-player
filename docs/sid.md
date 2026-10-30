# Building a `.sid`

`unitracker-sid FILE` (`unitracker_player.sid.build`) turns a document into a PSID v2 file that
plays the registers of [the player](semantics.md) on a C64, tick for tick.

## Pipeline

| step | module | |
|---|---|---|
| render | `render.py` | the player's 25 registers per frame, to the song's end, or for a looping song to the first frame its whole state recurs at its loop row |
| streams | `streams.py` | each cell that is ever nonzero as runs of a constant delta |
| grammar | `streams.py` | Re-Pair over every stream at once, then each rule cheaper copied than called inlined |
| assemble | `sid.py`, `asm/` | the streams' tick unrolled, the grammar as bytes, the playroutine, linked by llvm-mos |

### The loop proof

A looping song repeats from tick `L` with period `P` when the player's state at `L + P` equals
its state at `L`, every tick made relative. Modulators keep counting phi across passes, so each
modulator's phi is first reduced by its expression's period in phi (`qperiod.py`). A phi-only subterm
has `f(phi + Q) = f(phi) + D` from `phi >= P0`. The rules are: constants and phi; sums; products with a
constant or of periodic terms; `//` and `%` by a constant; and comparisons, `abs`, `min`, `max`,
`F` and tables, which settle once their argument's step carries it past every crossing. Each
signature is checked and minimised on its values. An expression whose every phi lies in subterms
with `D = 0` is periodic in phi from the largest `P0`. A song whose state does not recur in 1024
passes is refused; `--ticks N` builds `N` ticks instead.

### Cells

`freq1-3` and `pw1-3` are the register words, `ctrl1-3`, `ad1-3`, `sr1-3`, `resfilt` and
`modevol` the registers, and `cut` the 11-bit cutoff, written as `cut & 7` and `cut >> 3`.

## The playroutine

`init` (the load address) zeroes the SID, sets each stream at its start and installs a raster
IRQ through `$0314`; the PSID's play address is 0. Each IRQ runs `tick`: per stream, decrement
its count; when a run ends, `fetch` reads the next; if the delta is nonzero, add it and store
the register(s). Runs are encoded:

| bytes | meaning |
|---|---|
| `0nnnnnnn` | hold `n` ticks |
| `10nnnnnn d` | add `d` (sign-extended) on `n` ticks |
| `110nnnnn lo hi` | add the word on `n` ticks |
| `e0 lo hi` | call a rule |
| `e1 lo hi` | jump: a rule's last call, and a stream's loop |
| `e2` | return |
| `e3` | hold forever: an ending song's end |

Each stream has its own stack of return addresses, as deep as its deepest nesting of calls that
are not last in their rule, reached through a 16-bit pointer. The hot stream state (count,
delta, value) lives in zero page from `$02`.

## Tests

| test | checks |
|---|---|
| `test_streams.py` | runs rebuild each cell; the grammar plays every stream back |
| `test_qperiod.py` | every proved period holds on its values, for fixed and random expressions |
| `test_render.py` | rendered frames are `play()`'s; a looping song's frames repeat from its loop |
| `test_sid.py` | the assembled playroutine on llvm-mos's `mos-sim` plays the rendered frames, through the loop and one more period, within a PAL frame's cycles; the fixture songs' `.sid` files play them on the sidtrace oracle |

The fixtures are HVSC tunes named in `tests/conftest.py`. pysidtracker's cache holds the tunes,
their documents (lifted by unitracker, under `unitracker/` keyed by the tune's md5) and the
oracle traces. Tests needing llvm-mos skip without `mos-common-clang` or docker.
