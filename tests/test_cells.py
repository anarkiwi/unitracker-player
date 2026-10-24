"""A cell's repeated keys in their order, the reset stop and the registers before tick 0
(docs/semantics.md)."""

import pytest
from song import doc, pulse, reg

from unitracker_player import PlayError, play
from unitracker_player.document import read

PROGRAM = (
    "{ad: 9, sr: 240, ctrl: [{ctrl: 65, note: +0}, end], pw: [{pw: 100}, {wait: 2, pw: 3000}, end]}"
)


@pytest.mark.parametrize(
    "commands, expected",
    [("!pw:hold!pw:2000", [2000] * 8), ("!pw:2000!pw:hold", [0] * 8)],
)
def test_a_cell_s_commands_apply_in_the_order_written(commands, expected):
    """``hold`` then a binding stops the note's pulse program and binds; the binding then
    ``hold`` ends it, so the cell keeps its value."""
    text = doc(f"[C4.i1{commands}, -*3]", ins=PROGRAM)
    assert read(text).phrases["p1"][0].commands == tuple(
        ("pw", 2000 if c == "2000" else c) for c in commands.replace("!pw:", " ").split()
    )
    assert pulse(play(text, 8)) == expected


def test_a_row_s_repeated_keys_apply_in_the_order_written():
    ctrl = "[{ctrl: 65, note: +0, pw: hold, pw: 2000}, end]"
    text = doc(ins=f"{{ad: 9, ctrl: {ctrl}, pw: [{{pw: 100}}, {{wait: 2, pw: 3000}}, end]}}")
    row = read(text).instruments["i1"].ctrl.rows[0]
    assert [k for k, _ in row.keys] == ["ctrl", "note", "pw", "pw"]
    assert pulse(play(text, 8)) == [2000] * 8


@pytest.mark.parametrize("where", ["command", "row"])
def test_reset_restarts_phi_and_keeps_the_binding(where):
    """Binding the same modulator keeps its φ; ``reset`` takes it back to 0 on that tick."""
    keep = doc("[C4.i1!pw:'100*φ', -!pw:'100*φ', -*2]")
    if where == "command":
        reset = doc("[C4.i1!pw:'100*φ', -!pw:'100*φ'!pw:reset, -*2]")
    else:
        ctrl = "[{ctrl: 65, note: +0}, {wait: 5, pw: reset}, end]"
        reset = doc("[C4.i1!pw:'100*φ', -*3]", ins=f"{{ad: 9, ctrl: {ctrl}}}")
    assert pulse(play(keep, 12)) == [100 * t for t in range(12)]
    assert pulse(play(reset, 12)) == [100 * (t % 6) for t in range(12)]


def test_reset_of_an_unbound_slot_changes_nothing():
    assert pulse(play(doc("[C4.i1!pw:reset, -*3]"), 4)) == [0] * 4


def test_the_regs_line_is_the_registers_before_tick_0():
    """Each played voice's cells and the chip's start from the line; the bits the SID lacks
    read 0, and a voice the song does not play keeps its registers as given."""
    regs = [0x34, 0x12, 0x78, 0xF6, 0x41, 0x09, 0xF0] + [0xFF] * 7 + [0] * 7
    regs += [0xFF, 0x20, 0xF7, 0x1F]
    frames = play(doc("[-*4]", head=f"regs: {regs}\n"), 4)
    expected = regs[:3] + [0x06] + regs[4:21] + [7] + regs[22:]
    assert all(frames[t].tolist() == expected for t in range(4))


def test_a_note_drives_the_cells_the_regs_line_set():
    regs = [0x34, 0x12] + [0] * 23
    frames = play(doc(head=f"regs: {regs}\n"), 2)
    assert reg(frames, 0)[0] != 0x34 and reg(frames, 5) == [9, 9]


def test_a_regs_line_of_other_than_25_registers_is_refused():
    with pytest.raises(PlayError, match="regs: 3 values"):
        play(doc(head="regs: [1, 2, 3]\n"), 1)
