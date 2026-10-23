# Semantics

What the player does with each construct of the canonical grammar
([`canonical.lark`](https://github.com/anarkiwi/unitracker-grammar/blob/main/src/unitracker_grammar/canonical.lark)).
The output is the state of the 25 SID registers `$D400`-`$D418` at the end of each tick.

## Header

| line | meaning |
|---|---|
| `unitracker` | `1.20`; any other version is refused |
| `meta` | read and kept; it plays nothing |
| `clock` | `clock_hz` is the clock `F(k)` is computed at (default PAL, 985248); `cycles_per_tick` is kept and plays nothing: a tick is one frame |
| `voices` | 1 to 3 (default 3); more is refused. Voices number from 1; voice `i` writes `$D400 + 7(i - 1)` |
| `tempo` | `n` ticks per step, or `[a, b]`, steps alternating `a` and `b` (default 6); 0 is refused |
| `hold` | per cell, the events its modulators hold on (see [Holds](#holds)); none by default |
| `tables` | the tables expressions read as `T[k]` |
| `init` | one event per voice (see [Init](#init)); a count other than `voices` is refused |

## Initial state

Every register is 0, every cell (frequency, pulse width, cutoff, `ctrl`, the mask, the gate,
`ad`, `sr`, `res`, `route`, `mode`, `vol`) is 0, no voice has a note, an instrument, a program or
a modulator, and every voice's swing is at `a`.

## Definitions

Every `def` section of every row adds its names to one namespace for the whole song; a name
defined twice, and a reference to a name never defined, is refused. A `pw` or `filter` record,
and an instrument's `pw` or `filter` field, that is an expression is the program
`[{pw: e}, end]` or `[{cut: e}, end]`. A program `{loop: k}` with `k` outside its rows is
refused.

## Time

A tick is one frame. Each voice has its own tempo `(a, b)` and swing phase: a step of the voice
lasts `a` ticks and the next `b`, alternating (`n` is `(n, n)`). A step's length is fixed when
it begins, after the events of every voice at that tick have applied their tempo commands:
`!tempo` sets every voice's tempo and returns every voice's alternation to `a`, `!vtempo` this
voice's, from the step that begins at or after the command.

A voice's event applies at the first tick of its step, or earlier (see
[Hard restart](#hard-restart)); `*k` makes it last `k` steps, the steps after its first empty
but timed by the alternation.

## Timeline

A row starts at the tick the earliest-ending playing voice's phrase ends: the tick its next step
would begin. In that row every voice whose phrase ends at that tick has an entry and every
other voice has `~`; anything else is refused at that tick.

| entry | meaning |
|---|---|
| `p3` | the voice starts phrase `p3` |
| `p3+5`, `p3-2` | ... with every note of it transposed by that many semitones |
| `~` | the voice is still playing its phrase |
| `end` | the voice stops: it takes no more events; its programs and modulators keep running |

After the last row the ending applies as a row would: `{loop: k}` continues at row `k`, and
`end` stops every voice. The song ends at the tick every voice has stopped; that tick and those
after it are not played. A row count other than `voices`, and a song loop `k` outside the rows,
is refused.

`play(text, ticks=None)` plays an ending song to its end and refuses a looping one; with `ticks`
it plays that many ticks or to the song's end, whichever is first.

## Init

The `init` row's events apply at tick 0 before the events of the first step, in voice order,
exactly as events do, with no duration: `*k` there is ignored, and its tempo commands set the
tempo before the first step.

## Notes

A note name is format note `k = 12*octave + pitch class` (`C2` is 24, `B-1` is -1). The
voice's event note is the note plus its play entry's transpose. `N`, the sounding note, is the
absolute `note` of the last program row that set one, else the event note plus the relative
`note` of the last program row that set one (0 until then). A voice with neither has no `N`:
its frequency is not driven by `F(N)` and an expression reads `N` as 0.

| atom | meaning |
|---|---|
| a note | see below |
| `-` | no note |
| `off` | the gate goes off |
| `on` | the gate goes on |

`.iN` on any atom makes `iN` the voice's instrument from this event on; a note without it plays
the voice's last instrument. `-.iN`, `off.iN` and `on.iN` only set it.

A note without `!legato` sets the event note, clears the rows' relative and absolute note, sets
the gate on and starts the voice's instrument, if it has one:

- `ad` and `sr` take the instrument's;
- the control program restarts at its first row;
- the pulse program restarts unless the event carries `!pw`, the filter program unless it
  carries `!filter`;
- the frequency's program modulator becomes the instrument's `freq`, starting at φ = 0, or none;
- the voice's `!freq` and `!pw` commands end, and the `!cut` command if this voice gave it.

An instrument field it leaves out leaves what it would set as it is, but for `freq`: without one
the frequency is `F(N)`. A note with `!legato` sets only the event note: the gate, the rows'
note, the programs, the modulators and the commands are untouched, and no restart runs.

## Hard restart

A voice's restart program is the `restart` of its instrument as of the event (the event's
`.iN`, else the voice's last). It is anchored to every event of the voice without `!legato`
but a `-` without commands: its last row falls on the event's tick and each earlier row `i`
`hold_i + wait_(i+1)` ticks before row `i + 1`. The rows before the last are its lookahead.

A note runs the rows: each applies with its row meaning at its tick, before the voice's events
of that tick, even before the voice's previous event; a row that falls before tick 0 is
dropped; the last applies before the note and with a `ctrl` sets the gate on. The note and its
commands stay on its tick. Its `!ad` and `!sr` also apply with the last lookahead row, the
note's first-tick row: at that row's tick, right after it, so on that tick they win over the
row's own `ad` and `sr`; not where that row is dropped.

An `off`, `on` or `-` event runs no row: it applies its gate at the first lookahead row's tick
and its commands but tempo, `cut`, `resfilt`, `mode` and `vol` ones at the last lookahead row's,
each at tick 0 if that is earlier, as the voice's event of that tick; those others stay on its
tick, as does the whole event with no lookahead row. The restart's
ending is ignored.

## Programs

Each voice has three slots: control, pulse and filter. A program started at tick `s` applies
its first row at `s + wait`; each next row at the tick the previous row applied plus its `hold`
plus the row's `wait`; `hold` is 1 and `wait` 0 when left out. After its last row `end` stops
the slot, leaving every cell as it is, and `{loop: k}` continues at row `k`, whose `wait`
applies. A program of no rows stops the slot at once. A row due at a tick applies when its slot
is next visited at or after that tick; the rows a visit finds due apply in order, so `hold: 0`
runs the next row in the same tick; a visit that would apply one row twice is refused.

A row's keys apply in grammar order; a key it leaves out changes nothing.

| key | meaning |
|---|---|
| `ctrl: c` | the control byte becomes `c` (see [Registers](#registers)) |
| `ctrl: ^m`, `ctrl: c^m` | the mask becomes `m & $FE`; `c^m` also sets the control byte, `^m` leaves it; only a row with a mask changes the mask |
| `gate` | 0 off, any other value on |
| `note: +n`, `note: -n` | the relative note becomes `n`, clearing the absolute note |
| `note: G#5` | the absolute note becomes that note |
| `ad`, `sr` | the envelope byte |
| `pw: e` | the voice's pulse modulator becomes `e` (see [Modulators](#modulators)) |
| `pw: none` | stops the voice's pulse slot and its pulse modulator; the pulse width holds |
| `cut: e` | the cutoff's modulator becomes `e`, reading this voice's `N` |
| `res` | the resonance nibble, `$D417` bits 4-7 |
| `route: m` | the routing, `$D417` bits 0-3, becomes `m` |
| `route: +`, `route: + m` | adds this voice's bit (`1 << (voice - 1)`) and `m` |
| `route: '-'`, `route: '- m'` | removes them |
| `mode` | `$D418` bits 4-7 become the value's bits 4-7 |
| `freq: e` | the frequency's modulator becomes `e`; an integer `n` is `F(N) + n` |
| `run: c3`, `run: pw2`, `run: f1` | starts that program at this tick in the control, pulse or filter slot |
| `filter: none` | stops the voice's filter slot, and the cutoff's modulator if this voice started it; the cutoff holds |
| `vol` | the volume, `$D418` bits 0-3 |

Values wider than their field keep their low bits.

## Modulators

A modulator `e` drives one cell: a voice's frequency (16 bits), a voice's pulse width (12 bits)
or the one cutoff (11 bits). Each cell has a program modulator, set by program rows and
instruments, and a command modulator, set by commands, which owns the cell while it is set. The
owning modulator fires once per tick: `v' = e(v, φ, N)` wrapped to the cell's width, `v` the
cell's value before the firing, then φ + 1, but on its cell's hold ticks (see [Holds](#holds)).
A modulator starts with φ = 0 at the tick it is set and fires that tick,
unless it holds. A modulator that does
not own its cell does not fire and keeps its φ. A cell with no modulator holds its value, but
the frequency, which is `F(N)` then unless `!freq:hold` stopped it.

Expressions are compiled by `unitracker_grammar.expr` with `zero=True`. They are Python
integers: `//` and `%` are floored, and 0 when the divisor is 0; a
comparison is 1 or 0; `F(k)` is `unitracker_grammar.frequency(k, clock_hz)`, 0 below C0 and
where the word overflows; `T[k]` is 0 outside the table, and a table the document does not
define is refused.

## Holds

`hold: {freq: [...], pw: [...], cut: [...]}` names, per cell, the events on which its owning
modulator holds instead of firing; `ctrl.freq`, `ctrl.pw` and `ctrl.cut` name them for the
modulators a control program's row sets, and `freq`, `pw` and `cut` for every other. On a hold
tick φ does not advance and the cell takes the modulator's last firing again, with that firing's
`v` and φ and the current `N`, so a closed form of `N` and φ follows a change of `N` and a
modulator that reads `v` keeps its value. Before its first firing, a frequency modulator set on
the tick a note without `!legato` starts takes its value at φ = -1 with the cell's `v`; every
other keeps its cell's value. A shape (`mod:`) holds its tick `t` as a `freq` modulator. A
voice's events hold its frequency and pulse width modulators; the cutoff's are those of the
voice that set its modulator.

| event | the ticks of a voice it names |
|---|---|
| `step` | the first tick of each of its steps, the steps of an event's `*k` among them |
| `last` | the first tick of the last step of each phrase it plays |
| `read` | each step's first tick less the lookahead of the restart of the voice's instrument as of the step before (its first row's ticks before the note; 0 without a restart); none before the voice has an instrument |
| `note` | the tick a note without `!legato` applies |
| `legato` | the tick a note with `!legato` applies |
| `row` | a tick a row of its control program that sets a key other than `ctrl` and `gate` applies (a restart's rows are not its control program's) |

`e-k` is the tick `k` ticks before each tick of `e` (`row` takes no `-k`). The init row's events
are none of these.

## Commands

A command applies from the step that carries it, after its note, in grammar order.

| command | meaning |
|---|---|
| `!run:c3`, `!run:pw2`, `!run:f1` | starts that program in the slot its prefix names; refused on a voice that has no instrument yet |
| `!run:none` | stops the control slot |
| `!pw:n`, `!pw:xN` | the pulse width's command modulator becomes `n` (a constant) or `xN` |
| `!pw:none` | ends `!pw`, stops the pulse slot and the pulse modulator; the pulse width holds |
| `!filter:none` | stops the filter slot; ends this voice's `!cut` and the cutoff's program modulator if this voice started it; the cutoff holds |
| `!ad:n`, `!sr:n` | the envelope byte |
| `!ctrl:n` | the control byte, as a row's `ctrl` |
| `!resfilt:r/m` | `res` becomes `r` and `route` the mask `m` |
| `!mode:n`, `!vol:n` | as the row keys |
| `!cut:n`, `!cut:xN` | the cutoff's command modulator becomes `n` or `xN`, reading this voice's `N` |
| `!tempo:n`, `!tempo:a/b`, `!vtempo:...` | see [Time](#time) |
| `!freq:n`, `!freq:+n`, `!freq:-n` | the frequency's command modulator becomes `F(N) + n` |
| `!freq:xN` | ... becomes `xN` |
| `!freq:none` | ends `!freq`; the frequency's program modulator, or `F(N)`, drives it again |
| `!freq:hold` | ends `!freq` and the frequency's program modulator; nothing drives the frequency, which holds, until a note starts an instrument, a row sets `freq` or another `!freq` |
| `!legato` | see [Notes](#notes) |
| `!pw`, `!filter` | the note keeps its pulse / filter program (see [Notes](#notes)) |
| `*k` | the event lasts `k` steps |

A command modulator lasts until a note starts an instrument on its voice (for `!cut`, the voice
that gave it), another command of its key, or its `none`.

## Order within a tick

1. Events: at tick 0 the `init` events in voice order; then, per voice in order, the restart
   rows due this tick and the voice's events due this tick.
2. Programs: every voice's control slot, then every pulse slot, then every filter slot, each in
   voice order. A program started in a slot already visited this tick applies its rows from the
   next tick.
3. Modulators: every voice's frequency, then every pulse width, then the cutoff, each firing or
   holding (a `row` hold is a control-program row this tick's step 2 applied).
4. Registers.

## Registers

| register | value |
|---|---|
| `$D400 + 7(i-1)`, `+1` | frequency low, high |
| `+2`, `+3` | pulse width low, high (bits 8-11) |
| `+4` | `((ctrl ^ mask) & $FE) \| (ctrl & gate & 1)` |
| `+5`, `+6` | `ad`, `sr` |
| `$D415` | `cut & 7` |
| `$D416` | `cut >> 3` |
| `$D417` | `res << 4 \| route` |
| `$D418` | `mode \| vol` |
