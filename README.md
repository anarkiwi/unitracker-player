# unitracker-player

The generic player of canonical [unitracker](https://github.com/anarkiwi/unitracker) documents,
and their builder into standalone PSID `.sid` files.
It reads a document with the grammar of
[unitracker-grammar](https://github.com/anarkiwi/unitracker-grammar) and plays it to the state of
the 25 SID registers (`$D400`-`$D418`) at the end of each tick. It knows only the document: its
instruments, programs, expressions, phrases and timeline, and the note table `F(k)` at its clock.

## Install

```sh
pip install "unitracker-player @ git+https://github.com/anarkiwi/unitracker-player"
```

## Use

```python
from unitracker_player import load, play, Player

frames = play(text)               # np.uint8 array (ticks, 25), to the end of an ending song
frames = play(text, ticks=3000)   # a looping song needs ticks
song = load(text)                 # parse and compile once; play(song, ...) reuses it
player = Player(song)
regs = player.step()              # the 25 registers after one tick; None once the song ends
player.regs                       # the register file the last tick wrote, one store per register
```

It plays format 2.0, the grammar's one format. Code templates run as 6502 routines on a
[jennings](https://pypi.org/project/jennings/) MPU, with per-voice state from the document's
`state` line; templates, in the top-level `mod` section keyed by signatures (`vib(c, s): |`), are
bound by calls (`wob.freq: vib(3,40)`), and code reads the note table as `F.lo` / `F.hi`. A
program record may be a template keyed by its signature
(`arp(a, b): [{ctrl: 65, note: +a}, {wait: a, note: -b}, {loop: 0}]`), which a call binds
wherever a program's name stands (`ctrl: arp(7,4)`, `restart: hr(2,9,220)`, `run: arp(7,4)`).
Any definition may also stand inline at its one use (`C4.{ad: 9}`, `run: pw[{pw: 5}, end]`,
`play: [[C4, -*3]+2]`, `!pw:asm{lda phi; sta v; rts}`, `!pw:'v + 40'`) and plays as its named
form. A cell's repeated keys and commands apply in the order written, `reset` restarts a
binding's φ (`!pw:ptab(3)!pw:reset`), and a `regs` line gives the 25 registers before tick 0.

`read(text)` returns the document as plain dataclasses. A text outside the grammar raises
`unitracker_grammar.CanonicalError`; a document the player refuses raises `PlayError`.

```sh
unitracker-play song.yaml --ticks 3000 --out frames.npz   # array "frames"
unitracker-play song.yaml --out frames.csv                # one row per tick, d400..d418
unitracker-play song.yaml                                 # CSV to stdout
```

```sh
unitracker-sid song.yaml                         # song.sid
unitracker-sid song.yaml --out x.sid --ticks 3000 --load 0x2000
```

`unitracker-sid` renders the song here, proves where a looping song repeats, and assembles a
6502 playroutine (llvm-mos, locally or in the
[docker-mos-llvm-sdk](https://github.com/anarkiwi/docker-mos-llvm-sdk) image) whose raster IRQ
plays the same registers tick for tick.

## Docs

[docs/semantics.md](docs/semantics.md): the meaning of every construct of the grammar as played.
[docs/sid.md](docs/sid.md): how a document becomes a `.sid`, and the tests that check it.
`tools/corpus.py`: records the documents a test suite parses, plays a directory of them and
compares two such runs, to check that a change keeps what documents play.
