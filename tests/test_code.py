"""Format 1.23's code modulators assembled and fired on their 6502 (docs/semantics.md, Code
modulators)."""

import pytest
from jennings.disassembler import Disassembler
from jennings.opcodes import OPCODES
from unitracker_grammar.code import LEGAL, Imm, Label, Mem, Op
from unitracker_grammar.code import read as statements
from unitracker_grammar import frequency

from unitracker_player import PlayError, code
from unitracker_player.code import Machine

ACC = {"asl", "lsr", "rol", "ror"}
ZP = {(name.lower(), how) for name, how in OPCODES.values() if how.startswith("zp")}
T1 = {"T": (0x1234,)}


def routine(src: str, machine=None, bag=None, **layout):
    """Routine ``src`` (one statement per line) of freq on ``machine``, or a new one of the
    ``layout``'s ``tables`` and ``state``."""
    machine = machine or Machine(layout.get("tables", {}), layout.get("state", {}))
    names = {n for variables in machine.cells.values() for n in variables}
    return machine.compile("r", statements(src.strip() + "\n", names), bag or {}, "freq")


def fire(src: str, v=0, phi=0, note=0, event=0, **kwargs) -> int:
    """The ``v`` of one firing of ``src`` on zeroed state."""
    r = routine(src, **kwargs)
    return r(v, phi, note, event, bytearray(r.machine.sizes.get("freq", 0)))


def _operands(mnemonic: str, mode: str) -> list:
    """``(operand, address, disassembled operand)`` for the op: zero page ``v+1`` and absolute
    ``T.hi`` (at $0300) where the mode takes memory."""
    if mode == "implied":
        return [(None, None, " A" if mnemonic in ACC else "")]
    if mode == "immediate":
        return [(Imm(0x5A), None, " #$5a")]
    if mode == "label":
        return [("l", None, None)]
    index = mode.partition(",")[2]
    suffix = f",{index.upper()}" if index else ""
    zp = (mnemonic, "zp" + index) in ZP
    return [
        (Mem("v", 1, index), 1, (" $01" if zp else " $0001") + suffix),
        (Mem("T.hi", 0, index), 0x300, " $0300" + suffix),
    ]


@pytest.mark.parametrize("mnemonic, mode", sorted(LEGAL))
def test_every_legal_op_assembles_to_the_opcode_jennings_disassembles(mnemonic, mode):
    """Each (mnemonic, mode) is one opcode whose disassembly is the op back: zero page where
    its address is, else absolute (``stx``/``ldx ,y`` and ``sty``/``ldy ,x`` have only one)."""
    for operand, address, text in _operands(mnemonic, mode):
        machine = Machine(T1, {})
        body = (Label("l"), Op(mnemonic, mode, operand), Op("rts", "implied"))
        try:
            r = machine.compile("r", body, {}, "freq")
        except PlayError as exc:
            assert address == 0x300 and "no absolute form" in str(exc)
            assert (mnemonic, mode) in {("stx", "memory,y"), ("sty", "memory,x")}
            continue
        size, shown = Disassembler(machine.mpu).instruction_at(r.start)
        if text is None:
            text = f" ${r.start:04x}"
        assert shown == mnemonic.upper() + text
        assert size == r.end - r.start - 1


def test_the_entry_symbols_are_the_firing_s_values():
    """v in, phi φ's low 16 bits, nn and ne the notes' low bytes; v unwritten passes."""
    phi = "lda phi\nsta v\nlda phi+1\nsta v+1\nrts"
    assert fire(phi, phi=0x12345) == 0x2345
    assert fire("lda nn\nsta v\nlda ne\nsta v+1\nrts", note=-1, event=0x1C3) == 0xC3FF
    assert fire("rts", v=0xBEEF) == 0xBEEF


def test_each_firing_starts_with_zero_registers_and_clear_flags_but_i():
    """A, X, Y 0 and the flags clear but I whatever the firing before left."""
    machine = Machine({}, {})
    dirty = routine("lda #255\nldx #255\nldy #255\nsec\nsed\nclv\nrts", machine=machine)
    clean = routine("php\npla\nsta v\nstx v+1\nrts", machine=machine)
    regs = routine("sta v\nsty v+1\nrts", machine=machine)
    dirty(0, 0, 0, 0, bytearray())
    assert clean(0, 0, 0, 0, bytearray()) == 0x34
    dirty(0, 0, 0, 0, bytearray())
    assert regs(0xFFFF, 0, 0, 0, bytearray()) == 0


def test_state_is_read_and_stored_back_in_place():
    """The state bytes are the given copy at entry and written back to it at exit."""
    r = routine(
        "inc t\nlda t\nsta v\nlda q+1\nsta v+1\ninc q+1\nrts", state={"freq": (("t", 1), ("q", 2))}
    )
    state = bytearray([4, 0, 7])
    assert [r(0, 0, 0, 0, state) for _ in range(3)] == [0x0705, 0x0806, 0x0907]
    assert state == bytearray([7, 0, 10])


def test_other_writes_are_discarded_after_the_firing():
    """A table byte, a free zero page byte and the stack it pushed read as laid out again."""
    machine = Machine({"T": (7,)}, {})
    write = routine(
        "lda #9\nsta T.lo\nsta nn+100\nldx #3\nsta T.lo,x\npha\nlda T.lo\nsta v\npla\nrts",
        machine=machine,
    )
    read = routine("lda T.lo\nsta v\nlda nn+100\nldx #3\nora T.lo,x\nsta v+1\nrts", machine=machine)
    stack = routine("ldx #254\ntxs\npla\nsta v\nrts", machine=machine)
    assert write(0, 0, 0, 0, bytearray()) == 9
    assert read(0, 0, 0, 0, bytearray()) == 7
    assert stack(0x100, 0, 0, 0, bytearray()) == 0x100


def test_tables_read_their_entries_bytes_and_zero_past_them():
    """T.lo/T.hi are bits 0-7/8-15; T.lo+k is entry k past 256; indexing past a table reads 0."""
    tables = {"T": tuple(range(-1, 299)), "U": (5,)}
    both = "lda T.lo,y\nsta v\nlda T.hi,y\nsta v+1\nrts"
    assert fire(both, tables=tables) == 0xFFFF
    assert fire("lda T.lo+299\nsta v\nlda T.hi+299\nsta v+1\nrts", tables=tables) == 298
    assert fire("ldy #200\nlda U.lo,y\nsta v\nrts", v=0xABCD, tables=tables) == 0xAB00


def test_the_budget_is_4096_instructions_the_last_rts_included(monkeypatch):
    with pytest.raises(PlayError, match="4096"):
        fire("l:\njmp l\nrts")
    monkeypatch.setattr(code, "BUDGET", 3)
    assert fire("lda #1\nsta v\nrts") == 1
    with pytest.raises(PlayError, match="past 3"):
        fire("lda #1\nsta v\nnop\nrts")


@pytest.mark.parametrize(
    "src, match",
    [
        ("pla\nrts", "underflows"),
        ("lda #0\npha\nrts", "underflows"),
        ("l:\npha\njmp l\nrts", "overflows"),
        ("lda #0\npha\npha\nrts", "outside its code"),
        ("lda #0\nldx #255\nsta T.hi+8,x\nnop\nrts", r"opcode \$00"),
    ],
    ids=["pull", "rts-one-byte", "push", "rts-elsewhere", "self-modified-brk"],
)
def test_stack_misuse_and_running_other_code_are_refused(src, match):
    """The nop at $0407 overwritten with brk through T.hi's indexing is refused, and restored."""
    machine = Machine(T1, {})
    with pytest.raises(PlayError, match=match):
        routine(src, machine=machine)(0, 0, 0, 0, bytearray())
    assert machine.memory == machine.image


@pytest.mark.parametrize(
    "src, kwargs, match",
    [
        ("lda q\nsta v\nrts", {}, "unknown symbol q"),
        ("lda r\nsta v\nrts", {"state": {"pw": (("r", 1),)}}, "r is pw's state, not freq's"),
        ("l:\n" + "nop\n" * 127 + "bne l\nrts", {}, "out of branch range"),
        ("beq l\n" + "nop\n" * 128 + "l:\nrts", {}, "out of branch range"),
        ("lda #c\nsta v\nrts", {}, r"missing \['c'\]"),
        ("rts", {"bag": {"c": 1}}, r"unknown \['c'\]"),
        ("lda T.lo+65535\nrts", {"tables": T1}, "past memory"),
        ("rts", {"state": {"freq": tuple((f"s{i}", 2) for i in range(126))}}, "zero page"),
        ("rts", {"tables": {"T": (0,) * 32700}}, "overflow memory"),
        ("nop\n" * 30 + "rts", {"tables": {"T": (0,) * 32500}}, "overflow memory"),
    ],
    ids=[
        "unknown",
        "other-cell",
        "branch-back",
        "branch-ahead",
        "missing",
        "extra",
        "past",
        "zp",
        "tables",
        "code",
    ],
)
def test_refusals(src, kwargs, match):
    with pytest.raises(PlayError, match=match):
        fire(src, **kwargs)


def test_branches_reach_the_edges_of_their_range():
    """-128 back and +127 ahead assemble and run."""
    assert fire("l:\n" + "nop\n" * 126 + "beq l\nrts", v=7) == 7
    ahead = "lda #0\nbeq l\n" + "nop\n" * 127 + "l:\nlda #3\nsta v\nrts"
    assert fire(ahead) == 3


def test_immediates_take_a_parameter_s_low_and_high_bytes():
    """``#p`` and ``#<p`` its low byte, ``#>p`` its high, mod 65536."""
    src = "lda #<p\nsta v\nlda #>p\nsta v+1\nlda #p\nsta ne\nrts"
    assert fire(src, bag={"p": 0x1ABCD}) == 0xABCD
    assert fire(src, bag={"p": -2}) == 0xFFFE


def test_the_note_table_is_laid_out_once_before_the_first_routine_naming_it():
    """F.lo then F.hi, entry k F(k) at the machine's clock for k 0-255, only once named."""
    machine = Machine(T1, {}, 985000)
    routine("rts", machine)
    assert "F.lo" not in machine.symbols
    read_hi = routine("ldx #57\nlda F.hi,x\nsta v\nrts", machine)
    lo = machine.symbols["F.lo"]
    assert machine.symbols["F.hi"] == lo + 256 and lo == read_hi.start - 512
    words = [frequency(k, 985000) for k in range(256)]
    assert bytes(machine.image[lo : lo + 512]) == bytes(
        [w & 0xFF for w in words] + [w >> 8 for w in words]
    )
    assert read_hi(0, 0, 0, 0, bytearray()) == words[57] >> 8
    routine("lda F.lo\nsta v\nrts", machine)
    assert machine.symbols["F.lo"] == lo


def test_a_note_table_past_memory_is_refused():
    machine = Machine({"T": (0,) * 0x7E01}, {})
    with pytest.raises(PlayError, match="F: the tables and routines overflow memory"):
        routine("lda F.lo\nrts", machine)
