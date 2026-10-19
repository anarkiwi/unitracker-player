# unitracker-player

The generic player of canonical [unitracker](https://github.com/anarkiwi/unitracker) documents.
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
```

`read(text)` returns the document as plain dataclasses. A text outside the grammar raises
`unitracker_grammar.CanonicalError`; a document the player refuses raises `PlayError`.

```sh
unitracker-play song.yaml --ticks 3000 --out frames.npz   # array "frames"
unitracker-play song.yaml --out frames.csv                # one row per tick, d400..d418
unitracker-play song.yaml                                 # CSV to stdout
```

## Docs

[docs/semantics.md](docs/semantics.md): the meaning of every construct of the grammar as played.
