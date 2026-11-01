"""Play documents with the Python and the native call machine and report where the grids differ.

``python tools/native_diff.py [--ticks N] [--bench] DOC...`` prints, per document, the first
differing ``(frame, register)`` and the count of differing cells, or ``same``; with ``--bench``
also the microseconds per frame of each player.
"""

import argparse
import time
from pathlib import Path

import numpy as np

from unitracker_player.native import enabled, export, player, run
from unitracker_player.player import load, play_python


def native_grid(song, ticks: int) -> np.ndarray:
    """The native player's grid of ``ticks`` frames."""
    native = player(song)
    if native is None:
        raise RuntimeError("the native player does not play the document")
    return np.frombuffer(run(native, ticks), dtype=np.uint8).reshape(-1, 25)


def timed(call, *args):
    """``(result, seconds)`` of ``call(*args)``."""
    start = time.perf_counter()
    return call(*args), time.perf_counter() - start


# pylint: disable-next=too-many-locals
def compare(name: str, text: str, ticks: int, bench: bool) -> str:
    """One report line for document ``text``."""
    song = load(text)
    (stream, _), exported = timed(export, song)
    theirs, native_s = timed(native_grid, song, ticks)
    ours, python_s = timed(play_python, song, ticks)
    rows = min(len(ours), len(theirs))
    diff = np.argwhere(ours[:rows] != theirs[:rows])
    same = len(diff) == 0 and len(ours) == len(theirs)
    line = f"{name}: frames {len(ours)}/{len(theirs)} "
    line += "same" if same else f"differ {len(diff)} cells"
    if len(diff):
        frame, reg = (int(x) for x in diff[0])
        line += f" first frame {frame} register {reg}"
        line += f" python {ours[frame, reg]} native {theirs[frame, reg]}"
    if bench:
        us = 1e6 / max(len(ours), 1)
        line += f" | python {python_s * us:.1f} us/frame native {native_s * us:.2f} us/frame"
        line += f" export {exported * 1e3:.0f} ms ({len(stream)} ints)"
    return line


def main(argv=None) -> int:
    """Compare each document."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("docs", nargs="+", type=Path)
    parser.add_argument("--ticks", type=int, default=20000)
    parser.add_argument("--bench", action="store_true")
    args = parser.parse_args(argv)
    if not enabled():
        raise SystemExit("the native player is not built")
    for path in args.docs:
        print(compare(path.stem, path.read_text(), args.ticks, args.bench), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
