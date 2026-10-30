"""The observer of a Player's steps (docs/semantics.md, Observing)."""

from song import doc
from unitracker_player import Player, load
from unitracker_player.player import OBSERVED

CTRL = "{ctrl:[{ctrl:65},end]}"
SWEEP = "{ctrl:[{ctrl:65},end],pw:10*phi + 1}"


def observed(text: str, ticks: int) -> list:
    """Every ``(kind, tick, args)`` the player reports over ``ticks`` ticks."""
    log = []
    player = Player(load(text), lambda kind, *args: log.append((kind, player.tick, args)))
    for _ in range(ticks):
        player.step()
    return log


def of(log: list, kind: str, cell: str | None = None) -> list:
    """The ``(tick, args)`` of a kind's reports, those of ``cell`` only where given."""
    at = 0 if kind == "fire" else 1
    return [(t, a) for k, t, a in log if k == kind and cell in (None, a[at])]


def test_every_kind_is_documented_and_reported():
    log = observed(doc(phrase="[C4.i1 pw:'10*phi + 1',- pw:none,-]", ins=CTRL, head="tempo:1\n"), 3)
    assert {k for k, _, _ in log} <= set(OBSERVED)
    assert {"event", "bind", "stop", "fire", "cell", "enter", "leave", "row", "start"} == {
        k for k, _, _ in log
    }


def test_fire_reports_each_firing_with_its_phi_and_values():
    log = observed(doc(phrase="[C4.i1,-*3]", ins=SWEEP), 3)
    fires = of(log, "fire", "pw")
    assert [(t, a[0], a[1], a[2], a[3], a[5], a[6], a[7]) for t, a in fires] == [
        (0, "pw", 0, 0, 0, 0, 0, 1),
        (1, "pw", 0, 0, 0, 1, 1, 11),
        (2, "pw", 0, 0, 0, 2, 11, 21),
    ]


def test_bind_and_stop_are_reported_before_they_apply():
    log = observed(doc(phrase="[C4.i1 pw:'10*phi + 1',- pw:none,-]", ins=CTRL, head="tempo:1\n"), 3)
    assert of(log, "bind", "pw") == [(0, (0, "pw", 0, True))]
    assert of(log, "stop", "pw") == [(1, (0, "pw", "none", (0,)))]
    assert [t for t, _ in of(log, "fire", "pw")] == [0]


CHAINED = "{ctrl:c1,freq:vib(2)}"


def scoped(log: list) -> list:
    """The kinds between each ``enter`` and its ``leave``, as ``(scope, kinds)``."""
    out, stack = [], []
    for kind, _, args in log:
        if kind == "enter":
            stack.append((args[0], []))
        elif kind == "leave":
            out.append(stack.pop())
        elif stack:
            stack[-1][1].append(kind)
    return out


def test_starts_rows_rewinds_and_scopes_are_reported():
    text = doc(
        phrase="[C4.i1,-*7,D.i1,-*7]",
        ins=CHAINED,
        head="voices:1\ntempo:1\nslots:{freq:{freq:{reset:[note]}}}\n",
        mods={"vib(d)": "v + d*(phi % 4)"},
        ctrl={"c1": "[{ctrl:65},{wait:2,freq:vib(2)},end]"},
    )
    log = observed(text, 16)
    assert [(t, a[1]) for t, a in of(log, "start")] == [(0, 0), (8, 0)]
    assert [t for t, _ in of(log, "row")][:3] == [0, 3, 8]
    assert [(t, a) for t, a in of(log, "rewind")] == [(0, (0, "freq.freq")), (8, (0, "freq.freq"))]
    assert ("instrument", ["start", "bind"]) in scoped(log)
    assert ("apply", ["bind"]) in scoped(log)
