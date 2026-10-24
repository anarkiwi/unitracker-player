"""A corpus of documents, to check that a grammar or player change keeps what they play.

As a pytest plugin (``-p corpus``, ``tools`` on the path) it writes every text a suite parses to
``$UNITRACKER_CORPUS``; as a command it plays a directory's documents (``frames``), compares two
such runs (``diff``) and lists the documents that are not their canonical text (``canon``).
"""

import argparse
import hashlib
import os
import sys
from pathlib import Path

import numpy as np
from tqdm import tqdm


def pytest_configure(config):  # pylint: disable=unused-argument
    """Records every text the canonical parser parses into ``$UNITRACKER_CORPUS``."""
    from unitracker_grammar import canonical  # pylint: disable=import-outside-toplevel

    out = Path(os.environ["UNITRACKER_CORPUS"])
    out.mkdir(parents=True, exist_ok=True)
    lark = canonical.parser()
    lark.parse = _recording(lark.parse, out)


def _recording(parse, out: Path):

    def recorded(text, *args, **kwargs):
        if isinstance(text, str) and text.startswith("unitracker: "):
            name = hashlib.sha1(text.encode()).hexdigest()[:16]
            (out / f"{name}.yaml").write_text(text, encoding="utf-8")
        return parse(text, *args, **kwargs)

    return recorded


def frames(directory: Path, out: Path, ticks: int):
    """Plays each document of ``directory``; a refused one is recorded by its error's name."""
    from unitracker_player import play  # pylint: disable=import-outside-toplevel

    result = {}
    for path in tqdm(sorted(directory.glob("*.yaml")), unit="doc"):
        try:
            result[path.stem] = play(path.read_text(encoding="utf-8"), ticks)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            result[path.stem] = np.frombuffer(type(exc).__name__.encode(), np.uint8)
    np.savez_compressed(out, **result)


def diff(a: Path, b: Path) -> list[str]:
    """The names in both runs whose frames differ."""
    left, right = np.load(a), np.load(b)
    common = sorted(set(left.files) & set(right.files))
    print(f"{len(common)} common, {len(left.files)} / {len(right.files)} in each", file=sys.stderr)
    return [n for n in common if not np.array_equal(left[n], right[n])]


def canon(directory: Path) -> list[str]:
    """The documents of ``directory`` whose model does not print as their text."""
    from unitracker_grammar.document import printed, read  # pylint: disable=import-outside-toplevel

    out = []
    for path in tqdm(sorted(directory.glob("*.yaml")), unit="doc"):
        text = path.read_text(encoding="utf-8")
        try:
            same = printed(read(text)) == text
        except Exception:  # pylint: disable=broad-exception-caught
            same = False
        if not same:
            out.append(path.stem)
    return out


def main(argv=None) -> int:
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("frames")
    cmd.add_argument("directory", type=Path)
    cmd.add_argument("out", type=Path)
    cmd.add_argument("--ticks", type=int, default=3000)
    cmd = sub.add_parser("diff")
    cmd.add_argument("a", type=Path)
    cmd.add_argument("b", type=Path)
    cmd = sub.add_parser("canon")
    cmd.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    if args.command == "frames":
        frames(args.directory, args.out, args.ticks)
        return 0
    names = diff(args.a, args.b) if args.command == "diff" else canon(args.directory)
    print("\n".join(names))
    return 1 if names else 0


if __name__ == "__main__":
    sys.exit(main())
