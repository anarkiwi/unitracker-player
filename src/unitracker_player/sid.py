"""A standalone PSID ``.sid`` of a document: its registers rendered here, each cell's stream of
delta runs compressed to one grammar, and the playroutine of ``asm/`` assembled around it."""

import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path

from unitracker_grammar import NTSC_HZ, PAL_HZ

from unitracker_player import mos, streams
from unitracker_player.document import read
from unitracker_player.player import PlayError
from unitracker_player.render import Render, render

LOAD = 0x1000
RASTER = 0x30
ZP = 0x02
ASM = ("player.s", "link.ld")
OPS = {"call": 0xE0, "jump": 0xE1, "ret": 0xE2, "stop": 0xE3}


class SidError(PlayError):
    """A song whose playroutine does not assemble, as one too large for its load address."""


@dataclass(frozen=True)
class Plan:
    """A song's streams: the cells played, their grammar and each stream's stack depth."""

    cells: tuple
    grammar: streams.Grammar
    depths: tuple
    render: Render


def plan(rendered: Render) -> Plan:
    """The streams of the cells that are ever nonzero, compressed together."""
    cells, seqs = [], []
    loop = rendered.loop
    for name, regs, bits in streams.CELLS:
        values = streams.cell_values(rendered.frames, regs, name)
        if not values.any():
            continue
        runs = streams.runs(values, bits, [loop] if loop is not None else [])
        if loop is not None:
            at = next(i for i, t in enumerate(_starts(runs)) if t == loop)
            runs = runs[:at] + [streams.LOOP] + runs[at:]
        cells.append((name, regs, bits))
        seqs.append(runs + [streams.END])
    grammar = streams.inline(streams.repair(seqs))
    need: dict = {}
    depths = tuple(_stack(grammar, s, need, top=True) for s in grammar.starts)
    return Plan(tuple(cells), grammar, depths, rendered)


def _starts(runs: list) -> list:
    """The tick each run starts at, and the tick after the last."""
    out, t = [0], 0
    for _, n in runs:
        t += n
        out.append(t)
    return out


def _stack(grammar, seq: list, need: dict, top: bool = False) -> int:
    """The stack entries playing ``seq`` takes: a rule's last call is a jump."""
    out = 0
    for i, x in enumerate(seq):
        if isinstance(x, int):
            if x not in need:
                need[x] = _stack(grammar, grammar.rules[x], need)
            tail = not top and i == len(seq) - 1
            out = max(out, need[x] + (0 if tail else 1))
    return out


def _symbol(x, label: str, loop: bool, last: bool) -> str:
    if isinstance(x, int):
        op = "jump" if last else "call"
        return f"        .byte {OPS[op]:#04x}\n        .short r{x}\n"
    if x == streams.LOOP:
        return f"{label}_loop:\n"
    if x == streams.END:
        if loop:
            return f"        .byte {OPS['jump']:#04x}\n        .short {label}_loop\n"
        return f"        .byte {OPS['stop']:#04x}\n"
    d, n = x
    w = streams.width(d)
    if w == 0:
        return f"        .byte {n}\n"
    if w == 8:
        return f"        .byte {0x80 | n:#04x}, {d & 0xFF:#04x}\n"
    return f"        .byte {0xC0 | n:#04x}, {d & 0xFF:#04x}, {(d >> 8) & 0xFF:#04x}\n"


def _tick(s: int, regs: tuple, bits: int, name: str) -> str:
    """The unrolled tick of stream ``s``."""
    out = [
        f"; {name}",
        f"        dec cnt+{s}",
        "        bne 1f",
        f"        ldx #{s}",
        "        jsr fetch",
        f"1:      lda dlo+{s}",
    ]
    if bits == 16:
        out += [f"        ora dhi+{s}"]
    out += ["        beq 2f", "        clc", f"        lda vlo+{s}", f"        adc dlo+{s}"]
    out += [f"        sta vlo+{s}"]
    if name == "cut":
        out += [f"        lda vhi+{s}", f"        adc dhi+{s}", f"        sta vhi+{s}"]
        out += ["        sta tmp", f"        lda vlo+{s}", "        and #7", "        sta 0xd415"]
        out += [f"        lda vlo+{s}"] + ["        lsr tmp", "        ror"] * 3
        out += ["        sta 0xd416"]
    elif bits == 16:
        out += [f"        sta {0xD400 + regs[0]:#06x}", f"        lda vhi+{s}"]
        out += [
            f"        adc dhi+{s}",
            f"        sta vhi+{s}",
            f"        sta {0xD400 + regs[1]:#06x}",
        ]
    else:
        out += [f"        sta {0xD400 + regs[0]:#06x}"]
    return "\n".join(out + ["2:"]) + "\n"


def assembly(p: Plan) -> tuple:  # pylint: disable=too-many-locals
    """``(song.inc, song.s)``: the streams' equates, and their tick, tables and grammar."""
    n = len(p.cells)
    zp = {name: ZP + i * n for i, name in enumerate(("cnt", "dlo", "dhi", "vlo", "vhi"))}
    inc = [f"        .set {k}, {v:#04x}\n" for k, v in zp.items()]
    inc += [
        f"        .set {k}, {ZP + 5 * n + 2 * i:#04x}\n"
        for i, k in enumerate(("ptr", "sptr", "tmp"))
    ]
    inc += [f"        .set STREAMS, {n}\n", f"        .set RASTER, {RASTER:#04x}\n"]
    loop = p.render.loop is not None
    shared = "start stack_start ipl iph spl sph"
    out = [
        '        .include "song.inc"\n',
        f"        .globl {', '.join(shared.split())}\n",
        '        .section .text.tick,"ax",@progbits\n',
    ]
    out += ["        .globl tick\ntick:\n"]
    out += [_tick(s, regs, bits, name) for s, (name, regs, bits) in enumerate(p.cells)]
    out.append("        rts\n")
    out.append('        .section .rodata.song,"a",@progbits\n')
    out.append("start:\n" + "".join(f"        .short s{s}\n" for s in range(n)))
    bases = [2 * sum(p.depths[:s]) for s in range(n)]
    out.append("stack_start:\n" + "".join(f"        .short stack+{b}\n" for b in bases))
    for s, seq in enumerate(p.grammar.starts):
        out.append(f"s{s}:\n" + "".join(_symbol(x, f"s{s}", loop, False) for x in seq))
    for r, body in enumerate(p.grammar.rules):
        text = "".join(_symbol(x, "", loop, i == len(body) - 1) for i, x in enumerate(body))
        ret = "" if isinstance(body[-1], int) else f"        .byte {OPS['ret']:#04x}\n"
        out.append(f"r{r}:\n{text}{ret}")
    stack = max(2 * sum(p.depths), 1)
    out.append('        .section .bss.song,"aw",@nobits\n')
    for name, size in (("ipl", n), ("iph", n), ("spl", n), ("sph", n), ("stack", stack)):
        out.append(f"{name}:\n        .zero {size}\n")
    return "".join(inc), "".join(out)


def assemble(p: Plan, load: int = LOAD) -> bytes:
    """The playroutine's image at ``load``, its init first."""
    inc, song = assembly(p)
    with tempfile.TemporaryDirectory(prefix="ut-sid-") as tmp:
        build_dir = Path(tmp)
        mos.stage(build_dir, {"song.inc": inc, "song.s": song}, *ASM)
        try:
            mos.run(
                build_dir,
                f"mos-common-clang -nostdlib -T link.ld -Wl,--defsym=__load={load}"
                " -o song.bin player.s song.s",
            )
        except mos.ToolchainError as exc:
            raise SidError(str(exc)) from exc
        return (build_dir / "song.bin").read_bytes()


def psid(image: bytes, meta: dict, clock_hz: int, load: int = LOAD) -> bytes:
    """A PSID v2 file of an image at ``load``: one song, its init at ``load`` installing the
    interrupt that plays it (play address 0)."""
    clock = {PAL_HZ: 1, NTSC_HZ: 2}.get(clock_hz, 0)

    def text(key: str) -> bytes:
        return str(meta.get(key, "")).encode("latin-1", "replace")[:32].ljust(32, b"\0")

    head = struct.pack(">4sHHHHHHHI", b"PSID", 2, 0x7C, load, load, 0, 1, 1, 0)
    head += text("title") + text("author") + text("released")
    head += struct.pack(">HBBH", clock << 2, 0, 0, 0)
    return head + image


def build(text: str, ticks: int | None = None, load: int = LOAD, progress: bool = False) -> bytes:
    """The ``.sid`` of a document's text: to its end, looping from its proved loop, or
    ``ticks`` ticks then silent when given."""
    doc = read(text)
    p = plan(render(doc, ticks, progress))
    return psid(assemble(p, load), dict(doc.header.meta), doc.header.clock_hz or PAL_HZ, load)
