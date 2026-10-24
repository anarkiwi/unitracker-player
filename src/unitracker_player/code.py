"""Code modulators (format 1.23): a code template with its parameters assembled once into one
6502 memory per song, and run per firing on a jennings MPU (docs/semantics.md, Code
modulators)."""

from jennings.devices.mpu6502 import MPU
from jennings.opcodes import ILLEGAL_OPCODES, OPCODES
from unitracker_grammar import PAL_HZ, frequency
from unitracker_grammar.code import LEGAL, NOTES, Imm, Label, Mem, Op, params

from unitracker_player.sequencer import PlayError

BUDGET = 4096
SYMBOLS = {"v": 0, "phi": 2, "nn": 4, "ne": 5}
STATE = 6
STACK = 0x100
TABLES = 0x200
TOP = 0x10000
FLAGS = MPU.INTERRUPT
WRITES = frozenset("sta stx sty inc dec asl lsr rol ror".split())
_CHOICES = {
    "implied": ("impl", "acc"),
    "immediate": ("imm",),
    "memory": ("zp", "abs"),
    "memory,x": ("zpx", "absx"),
    "memory,y": ("zpy", "absy"),
}
ENCODE = {
    (name.lower(), how): opcode
    for opcode, (name, how) in OPCODES.items()
    if opcode not in ILLEGAL_OPCODES
}
_SIZE = {"impl": 1, "acc": 1, "imm": 2, "zp": 2, "zpx": 2, "zpy": 2, "rel": 2}
RUN, PUSH, PULL, RTS, BAD = range(5)
_RUNS = {
    ENCODE[m, how]
    for m, mode in LEGAL
    for how in _CHOICES.get(mode, ("rel", "abs"))
    if (m, how) in ENCODE
}
KIND = bytes(
    (
        (RTS if op == 0x60 else PUSH if op in (0x08, 0x48) else PULL if op in (0x28, 0x68) else RUN)
        if op in _RUNS
        else BAD
    )
    for op in range(256)
)


def _encoding(mnemonic: str, mode: str, address: int | None) -> tuple:
    """``(opcode, jennings mode)`` of an op: zero page where its address is and has the mode."""
    if mode == "label":
        how = "abs" if mnemonic == "jmp" else "rel"
        return ENCODE[mnemonic, how], how
    for how in _CHOICES[mode]:
        if (mnemonic, how) in ENCODE and (not how.startswith("zp") or address < 0x100):
            return ENCODE[mnemonic, how], how
    raise PlayError(f"{mnemonic} {mode}: no absolute form for address ${address:04x}")


def _bytes(value: int, byte: str) -> int:
    word = value & 0xFFFF
    return word >> 8 if byte == ">" else word & 0xFF


class Machine:
    """One 6502 and its memory for a song: ``v`` (2 bytes), ``phi`` (2), ``nn``, ``ne`` and
    from :data:`STATE` the state of the routine's cell in zero page; each table's ``T.lo`` then
    ``T.hi`` from :data:`TABLES`, each ``max(256, len(T))`` bytes, 0 past the table; then the
    routines' code, the note table ``F`` (``F(k)`` at ``clock_hz`` for k 0-255) laid out as a
    table before the first routine that names it. ``image`` is the memory as laid out, what
    every firing starts from."""

    def __init__(self, tables: dict, state: dict, clock_hz: int = PAL_HZ):
        self.clock_hz = clock_hz
        self.memory, self.symbols = bytearray(TOP), dict(SYMBOLS)
        self.cells, self.sizes = {}, {}
        for cell, variables in state.items():
            at, names = STATE, {}
            for name, size in variables:
                names[name], at = at, at + size
            if at > 0x100:
                raise PlayError(f"state: {cell}'s variables overflow zero page")
            self.cells[cell], self.sizes[cell] = names, at - STATE
        self.top = TABLES
        if self.top + sum(2 * max(256, len(values)) for values in tables.values()) > TOP:
            raise PlayError("tables: they overflow memory")
        for name, values in tables.items():
            self._table(name, values)
        self.image = bytearray(self.memory)
        self.mpu = MPU(self.memory, TABLES)

    def _table(self, name: str, values) -> None:
        """Lays out table ``name`` at the top: ``name.lo`` then ``name.hi``, each
        ``max(256, len(values))`` bytes."""
        span, at = max(256, len(values)), self.top
        if at + 2 * span > TOP:
            raise PlayError(f"{name}: the tables and routines overflow memory")
        for half, shift in (("lo", 0), ("hi", 8)):
            self.symbols[f"{name}.{half}"] = at
            self.memory[at : at + len(values)] = bytes((x >> shift) & 0xFF for x in values)
            at += span
        self.top = at

    def owner(self, symbol: str) -> str | None:
        """The cell whose state variable ``symbol`` is, else None."""
        return next((c for c, names in self.cells.items() if symbol in names), None)

    def address(self, symbol: str, cell: str, where: str) -> int:
        """The address of memory symbol ``symbol`` for a routine of ``cell``."""
        if symbol in self.symbols:
            return self.symbols[symbol]
        names = self.cells.get(cell, {})
        if symbol in names:
            return names[symbol]
        owner = self.owner(symbol)
        if owner is not None:
            raise PlayError(f"{where}: {symbol} is {owner}'s state, not {cell}'s")
        raise PlayError(f"{where}: unknown symbol {symbol}")

    def compile(self, name: str, statements: tuple, bag: dict, cell: str) -> "Routine":
        """Template ``name``'s ``statements`` with parameters ``bag``, bound to ``cell``,
        assembled after the code before it."""
        names = params(statements)
        if set(names) != set(bag):
            missing, extra = sorted(set(names) - set(bag)), sorted(set(bag) - set(names))
            raise PlayError(f"{name}: parameters: missing {missing}, unknown {extra}")
        if NOTES[0] not in self.symbols and any(
            isinstance(s, Op) and isinstance(s.operand, Mem) and s.operand.symbol in NOTES
            for s in statements
        ):
            start = self.top
            self._table("F", [frequency(k, self.clock_hz) for k in range(256)])
            self.image[start : self.top] = self.memory[start : self.top]
        ops, labels, end = self._layout(name, statements, cell)
        code, start = _emit(name, ops, labels, bag, self.top), self.top
        self.memory[start:end] = self.image[start:end] = code
        self.top = end
        return Routine(self, name, start, end, _dirty(ops, STATE + self.sizes.get(cell, 0)))

    def _layout(self, name: str, statements: tuple, cell: str) -> tuple:
        """Pass one: each op's ``(statement, opcode, mode, address)``, each label's address and
        the address after the code."""
        ops, labels, pc = [], {}, self.top
        for s in statements:
            if isinstance(s, Label):
                labels[s.name] = pc
                continue
            address = None
            if isinstance(s.operand, Mem):
                address = self.address(s.operand.symbol, cell, name) + s.operand.offset
                if address >= TOP:
                    raise PlayError(f"{name}: {s.operand.symbol}+{s.operand.offset} is past memory")
            opcode, how = _encoding(s.mnemonic, s.mode, address)
            ops.append((s, opcode, how, address))
            pc += _SIZE.get(how, 3)
        if pc > TOP:
            raise PlayError(f"{name}: the routines overflow memory")
        return ops, labels, pc


def _emit(name: str, ops: list, labels: dict, bag: dict, start: int) -> bytearray:
    """Pass two: the code's bytes at ``start``."""
    code = bytearray()
    for s, opcode, how, address in ops:
        at = start + len(code)
        code.append(opcode)
        if how == "rel":
            delta = labels[s.operand] - at - 2
            if not -128 <= delta < 128:
                raise PlayError(f"{name}: {s.mnemonic} {s.operand} is out of branch range")
            code.append(delta & 0xFF)
        elif s.mode == "label":
            code += labels[s.operand].to_bytes(2, "little")
        elif isinstance(s.operand, Imm):
            value = s.operand.value
            code.append(_bytes(bag[value] if isinstance(value, str) else value, s.operand.byte))
        elif address is not None:
            code += address.to_bytes(_SIZE.get(how, 3) - 1, "little")
    return code


def _dirty(ops: list, volatile: int) -> tuple:
    """The ``(start, end)`` ranges the routine may write that a firing does not set itself, the
    stack page where it pushes."""
    spans = []
    for s, _, how, address in ops:
        if s.mnemonic in ("pha", "php"):
            spans.append((STACK, TABLES))
        if s.mnemonic not in WRITES or address is None:
            continue
        if how in ("zpx", "zpy"):
            spans.append((0, 0x100))
        elif how in ("absx", "absy"):
            spans += [(address, min(address + 0x100, TOP)), (0, max(address + 0x100 - TOP, 0))]
        else:
            spans.append((address, address + 1))
    out = []
    for lo, hi in sorted((max(lo, volatile) if lo < STACK else lo, hi) for lo, hi in spans):
        if lo >= hi:
            continue
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return tuple(out)


class Routine:  # pylint: disable=too-few-public-methods
    """A code modulator: ``routine(v, phi, N, n, state)`` runs it on its machine with ``state``
    (a bytearray of its cell's variables, updated in place) and returns its ``v``, 16 bits."""

    __slots__ = ("machine", "name", "start", "end", "dirty")

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def __init__(self, machine: Machine, name: str, start: int, end: int, dirty: tuple):
        self.machine, self.name, self.start, self.end, self.dirty = machine, name, start, end, dirty

    def __call__(self, v: int, phi: int, note: int, event: int, state: bytearray) -> int:
        machine = self.machine
        mem, mpu = machine.memory, machine.mpu
        mem[0:STATE] = bytes(
            (v & 0xFF, v >> 8 & 0xFF, phi & 0xFF, phi >> 8 & 0xFF, note & 0xFF, event & 0xFF)
        )
        mem[STATE : STATE + len(state)] = state
        mpu.a = mpu.x = mpu.y = 0
        mpu.sp, mpu.p = 0xFF, FLAGS
        try:
            self._run(mem, mpu)
            state[:] = mem[STATE : STATE + len(state)]
            return mem[0] | mem[1] << 8
        finally:
            image = machine.image
            for lo, hi in self.dirty:
                mem[lo:hi] = image[lo:hi]

    def _run(self, mem, mpu):
        lo, hi, pc = self.start, self.end, self.start
        for _ in range(BUDGET):
            if not lo <= pc < hi:
                raise PlayError(f"{self.name}: runs outside its code, at ${pc:04x}")
            kind = KIND[mem[pc]]
            if kind:
                sp = mpu.sp
                if kind == RTS and sp == 0xFF:
                    return
                if kind == BAD:
                    raise PlayError(f"{self.name}: runs opcode ${mem[pc]:02x} at ${pc:04x}")
                if kind == PUSH and sp == 0:
                    raise PlayError(f"{self.name}: the stack overflows at ${pc:04x}")
                if sp >= (0xFE if kind == RTS else 0xFF) and kind != PUSH:
                    raise PlayError(f"{self.name}: the stack underflows at ${pc:04x}")
            mpu.pc = pc
            mpu.step()
            pc = mpu.pc
        raise PlayError(f"{self.name}: runs past {BUDGET} instructions")
