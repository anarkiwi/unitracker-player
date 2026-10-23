"""``unitracker-play FILE [--ticks N] [--out frames.npz|frames.csv]`` and
``unitracker-sid FILE [--out song.sid] [--ticks N] [--load ADDR]``."""

import argparse
import sys
from pathlib import Path

import numpy as np

from unitracker_player import sid
from unitracker_player.player import REGISTERS, load, play

HEADER = ",".join(f"d4{r:02x}" for r in range(REGISTERS))


def write(frames: np.ndarray, out) -> None:
    """``frames`` as an ``.npz`` (array ``frames``) or as CSV, one row per tick, to ``out``: a
    path or a text stream."""
    if isinstance(out, Path) and out.suffix == ".npz":
        np.savez_compressed(out, frames=frames)
    else:
        np.savetxt(out, frames, fmt="%d", delimiter=",", header=HEADER, comments="")


def main(argv=None) -> int:
    """Plays FILE and writes its registers per tick to ``--out``, or as CSV to stdout."""
    args = argparse.ArgumentParser(prog="unitracker-play", description=main.__doc__)
    args.add_argument("file", type=Path)
    args.add_argument("--ticks", type=int, help="ticks to play; required for a looping song")
    args.add_argument("--out", type=Path, help="frames.npz or frames.csv")
    opts = args.parse_args(argv)
    if opts.out is not None and opts.out.suffix not in (".npz", ".csv"):
        args.error(f"--out {opts.out}: write .npz or .csv")
    try:
        frames = play(load(opts.file.read_text(encoding="utf-8")), opts.ticks, progress=True)
    except ValueError as exc:
        args.error(f"{opts.file}: {exc}")
    write(frames, opts.out if opts.out is not None else sys.stdout)
    return 0


def sid_main(argv=None) -> int:
    """Builds FILE into a standalone PSID .sid: its playroutine installs a raster IRQ and plays
    the song's registers tick for tick."""
    args = argparse.ArgumentParser(prog="unitracker-sid", description=sid_main.__doc__)
    args.add_argument("file", type=Path)
    args.add_argument("--out", type=Path, help="the .sid (default FILE with suffix .sid)")
    args.add_argument("--ticks", type=int, help="play this many ticks and stop, not the loop")
    args.add_argument("--load", type=lambda x: int(x, 0), default=sid.LOAD, help="load address")
    opts = args.parse_args(argv)
    out = opts.out or opts.file.with_suffix(".sid")
    try:
        data = sid.build(opts.file.read_text(encoding="utf-8"), opts.ticks, opts.load, True)
    except ValueError as exc:
        args.error(f"{opts.file}: {exc}")
    out.write_bytes(data)
    print(f"{out}: {len(data)} bytes", file=sys.stderr)
    return 0
