"""A song played to its registers per tick with its loop proved: for a looping song, the tick
from which the frames repeat forever, found where the player's whole state recurs."""

from dataclasses import dataclass

import numpy as np
from tqdm import tqdm

from unitracker_grammar.expr import indexed

from unitracker_player.compiler import compile_document
from unitracker_player.player import REGISTERS, Player, PlayError, _Voice
from unitracker_player.qperiod import period

PASSES = 1024


class RenderError(PlayError):
    """A song whose frames do not provably repeat."""


@dataclass(frozen=True)
class Render:
    """The frames ``(ticks, 25)``; a looping song repeats ``frames[loop:]`` forever."""

    frames: np.ndarray
    loop: int | None


class _Periods:
    """Each expression's period in phi, proved once; a modulator's phi reduced by it."""

    def __init__(self, namespace: dict):
        self.namespace, self.memo = namespace, {}

    def phi(self, fn, phi: int) -> int:
        """``phi`` reduced by ``fn``'s period, where it has one and phi is past its start."""
        if id(fn) not in self.memo:
            node = getattr(fn, "node", None)
            self.memo[id(fn)] = None if node is None else period(node, self.namespace)
        found = self.memo[id(fn)]
        if found is None or phi < found[0]:
            return phi
        return found[0] + (phi - found[0]) % found[1]

    def mod(self, mod):
        """A modulator ``[fn, phi, ...]`` with its phi reduced."""
        if mod is None:
            return None
        return (id(mod[0]), self.phi(mod[0], mod[1]), *mod[2:])


def _freeze(obj, t: int):
    """A hashable image of player state, by identity for compiled song objects."""
    # pylint: disable=too-many-return-statements
    if obj is None or isinstance(obj, (int, str, bool)):
        return obj
    if isinstance(obj, (list, tuple)):
        return tuple(_freeze(x, t) for x in obj)
    if isinstance(obj, dict):
        return tuple(sorted((k, _freeze(v, t)) for k, v in obj.items()))
    return ("id", id(obj))


def _voice(voice: _Voice, c: int, periods: _Periods) -> tuple:
    state = dict(vars(voice))
    clocks = [c - lost for lost in state.pop("lost")]
    state["slots"] = [
        [p, i, due - k if p is not None else 0] for (p, i, due), k in zip(voice.slots, clocks)
    ]
    state["mods"] = {cell: [periods.mod(m) for m in mods] for cell, mods in voice.mods.items()}
    return _freeze(state, c)


def snapshot(player: Player, periods: _Periods) -> tuple:
    """The player's state at its current call, the registers it has yet to write included, every
    call and sequencer tick made relative to it and every modulator's phi reduced by its
    expression's period."""
    c, seq = player.call, player._seq  # pylint: disable=protected-access
    stick, full = player._stick, player.song.full  # pylint: disable=protected-access
    sequencer = (
        seq.tempo,
        seq.phase,
        [id(p) for p in seq.phrase],
        seq.pos,
        seq.transpose,
        seq.rest,
        [n - stick for n in seq.next],
        seq.stopped,
        seq.row,
        seq.entry,
        c % len(full) if full else 0,
    )
    pending = [
        {k - c: v for k, v in d.items() if v}
        for d in (player._events, player._lookahead)  # pylint: disable=protected-access
    ]
    mods = {cell: tuple(periods.mod(m) for m in ms) for cell, ms in player.song_mods.items()}
    voices = tuple(_voice(v, c, periods) for v in player.voices)
    ins = player._ins  # pylint: disable=protected-access
    held = {k: {x - c for x in v} for k, v in player._held.items() if v}  # pylint: disable=W0212
    delay = tuple(map(tuple, player._delay))  # pylint: disable=protected-access
    cells = (player.chip, player.owner, mods)
    return _freeze((voices, cells, sequencer, ins, pending, held, delay), c)


def render(doc, ticks: int | None = None, progress: bool = False) -> Render:
    """The frames of Document ``doc``: ``ticks`` of them when given (as an ending song), else
    to its end, or to the first recurrence of its state where it passes its loop (its loop row,
    an orderlist song's first looping voice's loop entry); a looping song with a code modulator
    needs ``ticks``."""
    # pylint: disable=too-many-locals
    song = compile_document(doc)
    if ticks is None and song.looping and song.code:
        raise RenderError("a looping song with a code modulator: its loop needs --ticks")
    namespace = {"_F": song.table, "_T": {k: indexed(v) for k, v in doc.tables}}
    periods = _Periods(namespace)
    player, rows = Player(song), []
    starts = player.passes
    seen: dict = {}
    with tqdm(total=ticks, unit="tick", disable=not progress) as meter:
        while ticks is None or len(rows) < ticks:
            regs = player.step()
            if regs is None:
                return Render(np.array(rows, dtype=np.uint8).reshape(-1, REGISTERS), None)
            rows.append(regs)
            meter.update()
            if ticks is None and starts and starts[-1] < player.ticks_played:
                state = snapshot(player, periods)
                starts.clear()
                if state in seen:
                    frames = np.array(rows, dtype=np.uint8).reshape(-1, REGISTERS)
                    return Render(frames[: player.tick], seen[state])
                if len(seen) == PASSES:
                    raise RenderError(f"no state recurs in {PASSES} passes of the loop")
                seen[state] = player.tick
    return Render(np.array(rows, dtype=np.uint8).reshape(-1, REGISTERS), None)
