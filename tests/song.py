"""Builds canonical documents for the tests and reads registers out of played frames."""

import re

import numpy as np
from unitracker_grammar.document import printed, read

HEAD = "unitracker: 2.0\nmeta: {title: T}\n"
TOP = ("mod", "restart", "ins", "ctrl", "pw", "filter")
I1 = "{ad: 9, sr: 240, ctrl: [{ctrl: 65, note: +0}, end]}"


# pylint: disable-next=too-many-arguments
def doc(
    phrase="[C4.i1, -*3]", ins=I1, rows=("[p1]",), ending="end", head="", *, mods=None, **defs
) -> str:
    """A parseable document: ``phrase`` and ``ins`` are p1 and i1 or a dict of names, ``mods``
    the mod section's templates by signature, ``defs`` the other top-level sections by kind,
    ``rows`` the play entries of each row, ``ending`` end or a loop row; one voice unless
    ``head`` says otherwise; in its one canonical text."""
    if "voices: " not in head:
        head = re.sub(r"^((?:clock: .*\n)?)", r"\1voices: 1\n", head)
    defs |= {"mod": mods or {}, "ins": ins if isinstance(ins, dict) else {"i1": ins}}
    head = _sections(head, defs)
    phrases = phrase if isinstance(phrase, dict) else {"p1": phrase}
    body = "".join(f"        {n}: {v}\n" for n, v in phrases.items())
    first, *rest = rows
    tail = "".join(f"  - play: {r}\n" for r in rest)
    end = "  - end\n" if ending == "end" else f"  - {{loop: {ending}}}\n"
    text = f"{HEAD}{head}song:\n  - def:\n      phrase:\n{body}    play: {first}\n{tail}{end}"
    return printed(read(text))


def _sections(head: str, defs: dict) -> str:
    """``head`` with the top-level sections of ``defs`` before its init line, if any."""
    top = "".join(
        f"{kind}:\n" + "".join(f"  {k}: {v}\n" for k, v in defs[kind].items())
        for kind in TOP
        if defs.get(kind)
    )
    at = head.find("init: ")
    return head + top if at < 0 else head[:at] + top + head[at:]


def word(frames: np.ndarray, lo: int) -> np.ndarray:
    """The 16-bit word of registers ``lo`` and ``lo + 1`` per tick."""
    return frames[:, lo].astype(int) | frames[:, lo + 1].astype(int) << 8


def freq(frames: np.ndarray, voice: int = 1) -> list[int]:
    """The voice's frequency word per tick."""
    return word(frames, 7 * (voice - 1)).tolist()


def pulse(frames: np.ndarray, voice: int = 1) -> list[int]:
    """The voice's 12-bit pulse width per tick."""
    return word(frames, 7 * (voice - 1) + 2).tolist()


def cutoff(frames: np.ndarray) -> list[int]:
    """The 11-bit cutoff per tick from $D415 bits 0-2 and $D416."""
    return (frames[:, 0x15].astype(int) & 7 | frames[:, 0x16].astype(int) << 3).tolist()


def reg(frames: np.ndarray, address: int) -> list[int]:
    """Register ``$D400 + address`` per tick."""
    return frames[:, address].tolist()


def timeline(starts: list[int], values: list, length: int) -> list:
    """The value of the latest start at or before each tick of ``length`` ticks."""
    return [values[max(i for i, s in enumerate(starts) if s <= t)] for t in range(length)]
