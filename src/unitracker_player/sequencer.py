"""The sequencer: a song's timeline walked to the tick each step begins."""


class PlayError(ValueError):
    """A document the player refuses, or a timeline that contradicts its phrases."""


class Sequencer:  # pylint: disable=too-many-instance-attributes
    """The timeline walked to the tick each event's step begins, independent of the
    registers; ``entry`` per voice its orderlist's next entry, ``passes`` the ticks the song
    passes its loop (its loop row starts, or the first looping voice's loop entry does) and
    ``looped`` per voice ``(tick, tempo, phase)`` at each start of its loop entry."""

    def __init__(self, song):
        n = self.voices = song.voices
        self.song = song
        self.tempo, self.phase = [song.tempo] * n, [0] * n
        self.phrase, self.pos, self.transpose = [()] * n, [0] * n, [0] * n
        self.rest, self.next, self.stopped = [0] * n, [0] * n, [False] * n
        self.row, self.end, self.entry, self.passes = 0, None, [0] * n, []
        self.looped = [[] for _ in range(n)]
        orders = song.orders or ()
        self.first = next((v for v, (_, loop) in enumerate(orders) if loop is not None), None)
        for v, event in enumerate(song.init):
            self._retime(v, event)

    def _retime(self, v: int, event):
        if event.tempo:
            self.tempo, self.phase = [event.tempo] * self.voices, [0] * self.voices
        if event.vtempo:
            self.tempo[v], self.phase[v] = event.vtempo, 0

    def _start_row(self, t: int, ending: set):
        if self.song.orders is not None:
            for v in sorted(ending):
                self._next_entry(t, v)
            if all(self.stopped):
                self.end = t
            return
        timeline = self.song.timeline
        if self.row == len(timeline):
            if self.song.loop is None:
                self.end = t
                return
            self.row = self.song.loop
        if self.row == self.song.loop:
            self.passes.append(t)
        index, self.row = self.row, self.row + 1
        for v, entry in enumerate(timeline[index]):
            if (entry[0] == "cont") == (v in ending):
                state = "ends its phrase" if v in ending else "is not at a phrase end"
                raise PlayError(f"row {index}: voice {v + 1} {state} at tick {t}")
            if entry[0] == "end":
                self.stopped[v] = True
            elif entry[0] == "phrase":
                self.phrase[v], self.pos[v], self.transpose[v] = entry[1], 0, entry[2]
        if all(self.stopped):
            self.end = t

    def _next_entry(self, t: int, v: int):
        """Voice ``v`` starts its orderlist's next entry at tick ``t``, or past its last
        continues at its loop entry or stops."""
        entries, loop = self.song.orders[v]
        if self.entry[v] == len(entries):
            if loop is None:
                self.stopped[v] = True
                return
            self.entry[v] = loop
        if self.entry[v] == loop:
            self.looped[v].append((t, self.tempo[v], self.phase[v]))
            if v == self.first:
                self.passes.append(t)
        entry = entries[self.entry[v]]
        self.phrase[v], self.pos[v], self.transpose[v] = entry[1], 0, entry[2]
        self.entry[v] += 1

    def advance(self, limit: int) -> list:
        """The steps that begin by tick ``limit``, as ``(tick, voice, event, transpose, last)``:
        ``event`` None for a step an event's ``*k`` times, ``last`` for a phrase's last step."""
        out = []
        while self.end is None:
            live = [v for v in range(self.voices) if not self.stopped[v]]
            t = min(self.next[v] for v in live)
            if t > limit:
                break
            due = [v for v in live if self.next[v] == t]
            ending = {v for v in due if not self.rest[v] and self.pos[v] == len(self.phrase[v])}
            if ending:
                self._start_row(t, ending)
                if self.end is not None:
                    break
                due = [v for v in due if not self.stopped[v]]
            for v in due:
                event = None
                if self.rest[v]:
                    self.rest[v] -= 1
                else:
                    event = self.phrase[v][self.pos[v]]
                    self.pos[v] += 1
                    self.rest[v] = event.steps - 1
                    self._retime(v, event)
                last = not self.rest[v] and self.pos[v] == len(self.phrase[v])
                out.append((t, v, event, self.transpose[v], last))
            for v in due:
                self.next[v] = t + self.tempo[v][self.phase[v]]
                self.phase[v] ^= 1
        return out


def loops(song) -> tuple:
    """Per voice of an orderlist song ``(loop entry, tick, period)``: the voice's steps from
    ``tick``, a start of its loop entry, repeat every ``period`` ticks, the first span between
    two starts of it at the same tempo and swing phase; None for a voice that ends."""
    seq, out = Sequencer(song), [None] * song.voices
    todo = {v for v, (_, loop) in enumerate(song.orders) if loop is not None}
    limit = 1
    while todo:
        seq.advance(limit)
        limit *= 2
        for v in list(todo):
            seen = {}
            for t, *key in seq.looped[v]:
                if tuple(key) in seen:
                    out[v] = (song.orders[v][1], seen[tuple(key)], t - seen[tuple(key)])
                    todo.discard(v)
                    break
                seen[tuple(key)] = t
    return tuple(out)
