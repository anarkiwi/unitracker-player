# Semantics

What the player does with each construct of the canonical grammar
([`canonical.lark`](https://github.com/anarkiwi/unitracker-grammar/blob/main/src/unitracker_grammar/canonical.lark)).
The output is the state of the 25 SID registers `$D400`-`$D418` at the end of each tick.

## Header

| line | meaning |
|---|---|
| `unitracker` | `1.23` and `1.22`, or `1.21` and `1.20` (see [Format 1.21](#format-121)); any other version is refused |
| `meta` | read and kept; it plays nothing |
| `clock` | `clock_hz` is the clock `F(k)` is computed at (default PAL, 985248); `cycles_per_tick` is kept and plays nothing: a tick is one frame |
| `voices` | 1 to 3 (default 3); more is refused. Voices number from 1; voice `i` writes `$D400 + 7(i - 1)` |
| `tempo` | `n` ticks per step, or `[a, b]`, steps alternating `a` and `b` (default 6); 0 is refused |
| `hold` | format 1.21's (see [Format 1.21](#format-121)); refused in `1.22` |
| `slots` | per cell, its modulator slots (see [Slots](#slots)); refused before `1.22` |
| `order` | the phases of a tick in the order they run (see [Order within a tick](#order-within-a-tick)); refused before `1.22` |
| `state` | per cell, the state variables code modulators read and write (see [Code modulators](#code-modulators)); refused before `1.23` |
| `tables` | the tables expressions read as `T[k]` and code as `T.lo` and `T.hi` |
| `init` | one event per voice (see [Init](#init)); a count other than `voices` is refused |

## Initial state

Every register is 0, every cell (frequency, pulse width, cutoff, `ctrl`, the mask, the gate,
`ad`, `sr`, `res`, `route`, `mode`, `vol`) is 0, no voice has a note, an instrument or a
program, every slot is unbound but where it has a default binding (see [Slots](#slots)), and
every voice's swing is at `a`.

## Definitions

Every `def` section of every row adds its names to one namespace for the whole song; a name
defined twice, and a reference to a name never defined, is refused. A `pw` or `filter` record,
and an instrument's `pw` or `filter` field, that is a binding (see [Bindings](#bindings)) is the
program `[{pw: b}, end]` or `[{cut: b}, end]`; an instrument's several `pw` or `filter` fields
are one such row binding each field's slot, and a program among them is refused. A program
`{loop: k}` with `k` outside its rows is refused.

A `mod` record is a template: an expression that may read parameters, lowercase names but `v`,
`n`, a function's and a record's name (`x1`, `pw1`, `f1`, ...). An `x` record is an instance:
an expression, or a template's name with the bag `{name: integer, ...}` naming each of its
parameters and no other, which is the template with each parameter its integer. An expression
outside a template that reads a parameter is refused. A `mod` record that is a code block is a
code template (see [Code modulators](#code-modulators)); one is refused before `1.23`.

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
- each `freq` field binds its slot of the frequency, or with `none` ends its binding.

Every binding of the instrument's fields, its `pw` and `filter` fields' too, starts φ at 0. No
other binding ends: an instrument field it leaves out leaves what it would set as it is. A
note with `!legato` sets only the event note: the gate, the rows' note, the programs and the
bindings are untouched, and no restart runs; it applies on its restart's last lookahead row's
tick (see [Hard restart](#hard-restart)).

## Hard restart

A voice's restart program is the `restart` of its instrument as of the event (the event's
`.iN`, else the voice's last). It is anchored to every event of the voice without `!legato`
but a `-` without commands: its last row falls on the event's tick and each earlier row `i`
`hold_i + wait_(i+1)` ticks before row `i + 1`, but for its first row, which falls on the step's
`read` tick (see [Events](#events)) where the voice has an instrument as of the step before. The
rows before the last are its lookahead.

A note runs the rows: each applies with its row meaning at its tick, before the voice's events
of that tick, even before the voice's previous event; a row that falls before tick 0 is
dropped; the last applies before the note and with a `ctrl` sets the gate on. The note and its
commands stay on its tick. Its `!ad`, `!sr` and `!ctrl` also apply with the last lookahead
row, the note's first-tick row: at that row's tick, right after it, so on that tick they win over
the row's own `ad`, `sr` and `ctrl`; not where that row is dropped. A `!legato` event runs no row
and applies whole on the last lookahead row's tick, at tick 0 if that is earlier.

An `off`, `on` or `-` event runs no row: it applies its gate at the first lookahead row's tick
and its commands at the last lookahead row's, each at tick 0 if that is earlier, as the voice's
event of that tick, but for its tempo, filter and volume ones (`!cut`, `!resfilt`, `!mode`, `!vol`,
`!filter:none` and a filter program's `!run`), which stay on its tick, as does the whole event
with no lookahead row. The restart's
ending is ignored.

## Programs

Each voice has three slots: control, pulse and filter; the filter runs one program, so a filter
program a voice starts stops every other voice's filter slot, as `!filter:none` there would. A
program started at tick `s` applies its first row at `s + wait`; each next row at the tick the
previous row applied plus its `hold` plus the row's `wait`; `hold` is 1 and `wait` 0 when left out.
After its last row `end` stops the slot, leaving every cell as it is, and `{loop: k}` continues at
row `k`, whose `wait` applies. A program of no rows stops the slot at once. A row due at a tick
applies when its slot is next visited at or after that tick; the rows a visit finds due apply in
order, so `hold: 0` runs the next row in the same tick; a visit that would apply one row twice is
refused.

A row's keys apply in grammar order, a cell's keys in the order written; a key it leaves out
changes nothing.

| key | meaning |
|---|---|
| `ctrl: c` | the control byte becomes `c` (see [Registers](#registers)) |
| `ctrl: ^m`, `ctrl: c^m` | the mask becomes `m & $FE`; `c^m` also sets the control byte, `^m` leaves it; only a row with a mask changes the mask |
| `gate` | 0 off, any other value on |
| `note: +n`, `note: -n` | the relative note becomes `n`, clearing the absolute note |
| `note: G#5` | the absolute note becomes that note |
| `ad`, `sr` | the envelope byte |
| `pw: b`, `slot.pw: b` | binds the voice's pulse width slot to `b` (see [Bindings](#bindings)) |
| `cut: b`, `slot.cut: b` | binds the cutoff's slot to `b`, reading this voice's notes |
| `pw: none`, `cut: none`, `freq: none` | see [Stops](#stops) |
| `pw: hold`, `cut: hold`, `freq: hold` | see [Stops](#stops) |
| `res` | the resonance nibble, `$D417` bits 4-7 |
| `route: m` | the routing, `$D417` bits 0-3, becomes `m` |
| `route: +`, `route: + m` | adds this voice's bit (`1 << (voice - 1)`) and `m` |
| `route: '-'`, `route: '- m'` | removes them |
| `mode` | `$D418` bits 4-7 become the value's bits 4-7 |
| `freq: b`, `slot.freq: b` | binds the voice's frequency slot to `b`; an integer `n` is `F(N) + n` |
| `run: c3`, `run: pw2`, `run: f1` | starts that program at this tick in the control, pulse or filter slot |
| `vol` | the volume, `$D418` bits 0-3 |

Values wider than their field keep their low bits.

## Slots

`slots: {cell: {name: {mod: m, hold: [...], reset: [...]}, ...}, ...}` names, per cell (`freq`,
`pw`, `cut`), its slots in firing order, each with its default binding `m` and the events it
holds and resets on (see [Events](#events)), any of them left out. A cell left out has one
slot, named after the cell, with neither. A cell that names a slot twice, and a `row.KEY` whose
`KEY` is no row key, is refused.

A slot with a default binding is bound to it at tick 0, before the `init` row: each voice's
frequency and pulse width slots by that voice, the cutoff's by voice 1.

## Bindings

A slot is bound by a program row's key, an instrument's field or a command of its cell (see
[Programs](#programs), [Notes](#notes), [Commands](#commands)); a `slot.` prefix names the slot,
and a key without one binds the cell's first slot. A prefix naming no slot of the cell is
refused. A binding names its modulator:

| binding | modulator |
|---|---|
| `x3` | the instance `x3` |
| `note` | the template `note`, which reads no parameter |
| `porta {s: 3}` | the template `porta` with its bag, as an instance (not in a command) |
| `v + 40`, `512` | the expression (not in a command, which takes an integer and an `x` name) |

A command or a program row binding a slot to the modulator it is bound to (the same expression
with the same parameters) keeps its φ; any other binding, a slot's first and a binding of a
note's instrument's fields start φ at 0 on that tick. A binding lasts
until its slot is bound again or stopped (see [Stops](#stops)). The voice that binds a slot of the
cutoff is the voice whose notes and events it reads.

## Modulators

A modulator `e` drives one cell: a voice's frequency (16 bits), a voice's pulse width (12 bits)
or the one cutoff (11 bits). Each tick, each cell's slots fire in their order. A bound slot first
takes φ = 0 on its reset events, then, but on its hold events, fires
`v' = e(v, φ, N, n)` wrapped to the cell's width, `v` the value the slot before it left (the
cell's value for the first), then φ + 1. A held or unbound slot passes `v` through and its φ does
not advance. The cell takes the last slot's value. A frequency with no bound slot is `F(N)`, but
after a `hold` (see [Stops](#stops)); a pulse width or cutoff with none holds its value.

`N` is the sounding note and `n` the event note (see [Notes](#notes)), each 0 where the voice has
none, of the voice that bound the slot. Expressions are compiled by `unitracker_grammar.expr`
with `zero=True`. They are Python integers: `//` and `%` are floored, and 0 when the divisor is
0; a comparison is 1 or 0; `F(k)` is `unitracker_grammar.frequency(k, clock_hz)`, 0 below C0
and where the word overflows; `T[k]` is 0 outside the table, and a table the document does not
define is refused.

## Code modulators

A code template is an NMOS 6502 routine (`unitracker_grammar.code`) fired where an expression
would be; its parameters are its immediates, bound by a bag as an expression template's. Each
voice has, per cell of the `state` line, its own copy of the cell's variables, and the song one
copy of the cutoff's; every byte is 0 at tick 0, and only code reads and writes it. A firing runs
on the copy of the voice that bound the slot (the one copy for `cut`), so the state persists
across firings, slots, bindings and notes; φ restarts as for any modulator, the state never does.

| symbol | bytes | at entry | at exit |
|---|---|---|---|
| `v` | 2 (`v`, `v+1`) | the value the slot before left | wrapped to the cell's width, the slot's value |
| `phi` | 2 | φ mod 65536 | discarded |
| `nn`, `ne` | 1 each | `N & 255`, `n & 255` | discarded |
| a state variable of the slot's cell | its size | the voice's copy | stored back to it |
| `T.lo`, `T.hi` | per entry | bits 0-7, 8-15 of entry `k` at `+k`; 0 past the table | discarded |

At entry A, X and Y are 0, the flags are clear but I and the stack is empty. The routine runs
from its first statement to an `rts` on an empty stack; every other write is discarded at exit.
The player lays `v`, `phi`, `nn`, `ne` and the state in zero page, then the tables and the code
in one 64K memory per song, and picks zero page or absolute for each memory operand. Refused: a
symbol that is none of these or a state variable of another cell than the binding's, a branch out
of range, a bag that names other than the routine's parameters, more than 4096 instructions in a
firing (the last `rts` among them), a pull or `rts` on an empty stack, a 257th byte pushed, and
running outside the routine's code. A looping song with a code modulator has no proved loop:
`unitracker-sid` needs `--ticks` for it.

A shape (`mod:` of an instrument) drives the frequency in place of its slots from the note
that arms it until a `!freq` binding or a `hold` of `freq`, holding as the first slot.

## Events

A slot's `hold` and `reset` events, and format 1.21's hold line, name ticks of the voice that
bound the slot:

| event | the ticks of a voice it names |
|---|---|
| `step` | the first tick of each of its steps, the steps of an event's `*k` among them |
| `last` | the first tick of the last step of each phrase it plays |
| `read` | each step's first tick less the lookahead of the restart of the voice's instrument as of the step before (its first row's ticks before the note; 0 without a restart); none before the voice has an instrument |
| `note` | the tick a note without `!legato` applies |
| `legato` | the tick a note with `!legato` applies |
| `row` | a tick a row of its control program that sets a key other than `ctrl` and `gate` applies (a restart's rows are not its control program's) |
| `row.KEY` | a tick a row of its control program that sets `KEY` applies; `row.freq` a row binding any slot of `freq` |

`e-k` is the tick `k` ticks before each tick of `e` (`row` and `row.KEY` take no `-k`). The init
row's events are none of these.

## Commands

A command applies from the step that carries it, after its note, in grammar order.

| command | meaning |
|---|---|
| `!run:c3`, `!run:pw2`, `!run:f1` | starts that program in the slot its prefix names; refused on a voice that has no instrument yet |
| `!run:none` | stops the control slot |
| `!pw:n`, `!pw:xN`, `!slot.pw:...` | binds the pulse width's slot to `n` (a constant) or `xN` |
| `!ad:n`, `!sr:n` | the envelope byte |
| `!ctrl:n` | the control byte, as a row's `ctrl` |
| `!resfilt:r/m` | `res` becomes `r` and `route` the mask `m` |
| `!mode:n`, `!vol:n` | as the row keys |
| `!cut:n`, `!cut:xN`, `!slot.cut:...` | binds the cutoff's slot to `n` or `xN`, reading this voice's notes |
| `!pw:none`, `!cut:none`, `!freq:none` | see [Stops](#stops) |
| `!pw:hold`, `!cut:hold`, `!freq:hold` | see [Stops](#stops) |
| `!tempo:n`, `!tempo:a/b`, `!vtempo:...` | see [Time](#time) |
| `!freq:n`, `!freq:+n`, `!freq:-n`, `!slot.freq:...` | binds the frequency's slot to `F(N) + n` |
| `!freq:xN` | ... to `xN` |
| `!legato` | see [Notes](#notes) |
| `!pw`, `!filter` | the note keeps its pulse / filter program (see [Notes](#notes)) |
| `*k` | the event lasts `k` steps |

A command's binding lasts as any other (see [Bindings](#bindings)).

## Stops

Each modulated cell, `freq`, `pw` and `cut`, takes the same two stops, as a row key, as an
instrument's `none` field and as a command; the cell's program slot is the pulse slot for `pw`,
the filter slot for `cut` and none for `freq`.

| stop | meaning |
|---|---|
| `none`, `slot.cell: none`, `!slot.cell:none` | ends the binding of the slot (its prefix's, else the first) |
| `hold`, `!cell:hold` | ends the binding of every slot of the cell and stops the voice's program slot of the cell |

After a `hold` the frequency holds: it is not `F(N)` until a note starts an instrument, a `freq`
slot is bound or a `none` of `freq`; a `hold` of `freq` also ends the instrument's shape. The
cutoff's stops end its bindings only where this voice bound them, and a filter program a voice
starts ends the cutoff bindings the other voices' rows made.

## Format 1.21

A `1.21` document has no `slots` line, slot prefix, `mod` record or template binding; its
`hold` line names, per cell, the events its modulators hold on, `ctrl.freq`, `ctrl.pw` and
`ctrl.cut` those a control program's row binds and `freq`, `pw` and `cut` every other. Each cell
has two slots: a program slot, bound by rows and instruments, and a command slot, bound by
commands. While the command slot is bound it owns the cell, else the program slot does; only
the owner fires, and an owner on a hold tick takes its last firing again, with that firing's
`v` and φ and the current `N`, so a closed form of `N` and φ follows a change of `N` and a
modulator that reads `v` keeps its value. Before its first firing, a frequency modulator bound
on the tick a note without `!legato` starts takes its value at φ = -1 with the cell's `v`;
every other keeps its cell's value. Every binding starts φ at 0.

A note that starts an instrument binds the frequency's program slot to the instrument's `freq`,
or unbinds it, and ends the voice's command bindings, but the pulse width's where it keeps the
pulse program, and the cutoff's where this voice made it.
A row's `none` ends the program slot's binding and a command's the command slot's; a row's
`hold` ends the program slot's and a command's both, each also stopping the voice's program
slot of the cell. Slot defaults, reset events and the order of slots do not apply.

### Format 1.20

A `1.20` document plays as `1.21` but for two spellings: its `pw: none` and `!pw:none` are
`1.21`'s `pw: hold` and `!pw:hold`, and its `filter: none` and `!filter:none` `1.21`'s
`cut: hold` and `!cut:hold`. A `1.21` or `1.22` document's `filter: none` and `!filter:none`
are refused.

## Order within a tick

Each tick runs its phases in order, each over the voices in voice order, then writes the
registers. The phases are:

| phase | what it runs |
|---|---|
| `events` | at tick 0 the `init` events; the voice's restart rows due this tick but its read row, then its events due this tick |
| `ctrl`, `pulse`, `filter` | the voice's control, pulse or filter program slot (see [Programs](#programs)) |
| `freq`, `pw`, `cut` | the cell's modulator slots in their order (see [Modulators](#modulators)); `cut` once, for the one cutoff |
| `read` | the read row: a note's restart's first lookahead row, on its tick, the step's read tick (see [Hard restart](#hard-restart)) |

The order is `[events, ctrl, pulse, filter, freq, pw, cut]`, the read row running within
`events`, unless the document's `order` line names every phase, `read` among them, exactly once;
any other `order` line is refused. A program or binding a phase starts after its own slot's
phase has run this tick applies from the next tick, and a `row` or `row.KEY` event is a control
program row the `ctrl` phase applied before that tick's modulator phase.

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
