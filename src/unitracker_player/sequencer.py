"""The sequencer: a song's timeline walked to the tick each step begins."""


class PlayError(ValueError):
    """A document the player refuses, or a timeline that contradicts its phrases."""


class Sequencer:
    """The timeline walked to the tick each event's step begins, independent of the
    registers."""

    def __init__(self, song):
        n = self.voices = song.voices
        self.song = song
        self.tempo, self.phase = [song.tempo] * n, [0] * n
        self.phrase, self.pos, self.transpose = [()] * n, [0] * n, [0] * n
        self.rest, self.next, self.stopped = [0] * n, [0] * n, [False] * n
        self.row, self.end = 0, None
        for v, event in enumerate(song.init):
            self._retime(v, event)

    def _retime(self, v: int, event):
        if event.tempo:
            self.tempo, self.phase = [event.tempo] * self.voices, [0] * self.voices
        if event.vtempo:
            self.tempo[v], self.phase[v] = event.vtempo, 0

    def _start_row(self, t: int, ending: set):
        timeline = self.song.timeline
        if self.row == len(timeline):
            if self.song.loop is None:
                self.end = t
                return
            self.row = self.song.loop
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
