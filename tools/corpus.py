"""A corpus of documents, to check that a grammar or player change keeps what they play.

As a pytest plugin (``-p corpus``, ``tools`` on the path) it writes every text a suite parses to
``$UNITRACKER_CORPUS``; as a command it plays a directory's documents (``frames``), compares two
such runs (``diff``), lists the documents that are not their canonical text (``canon``), and
carries documents across a change of the format's text: ``models`` pickles a directory's
document models under the grammar installed, ``reprint`` prints them under another into a
directory, listing those whose text does not read back to their model.
"""

import argparse
import hashlib
import os
import pickle
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
        if isinstance(text, str) and text.startswith("unitracker:"):
            name = hashlib.sha1(text.encode()).hexdigest()[:16]
            (out / f"{name}.yaml").write_text(text, encoding="utf-8")
        return parse(text, *args, **kwargs)

    return recorded


def frames(directory: Path, out: Path, ticks: int, first: bool = False):
    """Plays each document of ``directory``, each with its definitions at their first use where
    ``first``; a refused one is recorded by its error's name."""
    from unitracker_player import play  # pylint: disable=import-outside-toplevel

    result = {}
    for path in tqdm(sorted(directory.glob("*.yaml")), unit="doc"):
        try:
            text = path.read_text(encoding="utf-8")
            result[path.stem] = play(_first_use(text) if first else text, ticks)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            result[path.stem] = np.frombuffer(type(exc).__name__.encode(), np.uint8)
    np.savez_compressed(out, **result)


def _first_use(text: str) -> str:
    """``text`` with its definitions at their first use."""
    # pylint: disable-next=import-outside-toplevel
    from unitracker_grammar.document import first_use, printed, read

    return printed(first_use(read(text)))


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


def models(directory: Path, out: Path) -> list[str]:
    """Pickles ``{name: model}`` of the documents of ``directory`` the grammar reads into
    ``out``; the names of those it refuses."""
    from unitracker_grammar.document import read  # pylint: disable=import-outside-toplevel

    found, refused = {}, []
    for path in tqdm(sorted(directory.glob("*.yaml")), unit="doc"):
        try:
            found[path.stem] = read(path.read_text(encoding="utf-8"))
        except ValueError:
            refused.append(path.stem)
    out.write_bytes(pickle.dumps(found))
    return refused


def reprint(pickled: Path, directory: Path) -> list[str]:
    """Writes each model of ``pickled`` (:func:`models`) as its canonical text into
    ``directory``; the names of those whose text does not read back to the model and print as
    itself."""
    from unitracker_grammar.document import printed, read  # pylint: disable=import-outside-toplevel

    directory.mkdir(parents=True, exist_ok=True)
    out = []
    for name, model in tqdm(pickle.loads(pickled.read_bytes()).items(), unit="doc"):
        text = printed(model)
        (directory / f"{name}.yaml").write_text(text, encoding="utf-8")
        try:
            again = read(text)
        except ValueError as exc:
            out.append(f"{name}: {exc}")
            continue
        if again != model or printed(again) != text:
            out.append(name)
    return out


def main(argv=None) -> int:
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("frames")
    cmd.add_argument("directory", type=Path)
    cmd.add_argument("out", type=Path)
    cmd.add_argument("--ticks", type=int, default=3000)
    cmd.add_argument("--first-use", action="store_true", help="each definition at its first use")
    cmd = sub.add_parser("diff")
    cmd.add_argument("a", type=Path)
    cmd.add_argument("b", type=Path)
    cmd = sub.add_parser("canon")
    cmd.add_argument("directory", type=Path)
    cmd = sub.add_parser("models")
    cmd.add_argument("directory", type=Path)
    cmd.add_argument("out", type=Path)
    cmd = sub.add_parser("reprint")
    cmd.add_argument("pickled", type=Path)
    cmd.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    if args.command == "frames":
        frames(args.directory, args.out, args.ticks, args.first_use)
        return 0
    commands = {
        "diff": lambda: diff(args.a, args.b),
        "canon": lambda: canon(args.directory),
        "models": lambda: models(args.directory, args.out),
        "reprint": lambda: reprint(args.pickled, args.directory),
    }
    names = commands[args.command]()
    print("\n".join(names))
    return 1 if names else 0


if __name__ == "__main__":
    sys.exit(main())
