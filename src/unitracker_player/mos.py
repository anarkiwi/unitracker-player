"""The llvm-mos toolchain, local or in the SDK's container, and the playroutine on its simulator:
the registers and the cycles of every tick."""

import os
import shutil
import struct
import subprocess
from pathlib import Path

import numpy as np

MOS_IMAGE = "ghcr.io/anarkiwi/docker-mos-llvm-sdk:23.3.0"
ASM_DIR = Path(__file__).resolve().parent / "asm"
SIM_LOAD = 0x1000


class ToolchainError(RuntimeError):
    """A failed llvm-mos command."""


def run(build_dir: Path, script: str) -> subprocess.CompletedProcess:
    """Runs shell ``script`` in ``build_dir`` with the llvm-mos tools on PATH: locally when
    ``mos-common-clang`` is there, else in the SDK's container with ``build_dir`` mounted."""
    path = str(build_dir)
    if shutil.which("mos-common-clang"):
        cmd = ["sh", "-c", script]
    else:
        user = f"{os.getuid()}:{os.getgid()}"
        cmd = ["docker", "run", "--rm", "-u", user, "-v", f"{path}:{path}", "-w", path]
        cmd += ["--entrypoint", "sh", MOS_IMAGE, "-c", script]
    out = subprocess.run(cmd, cwd=path, capture_output=True, text=True, check=False)
    if out.returncode:
        raise ToolchainError(f"llvm-mos: {out.stderr.strip() or out.stdout.strip()}")
    return out


def stage(build_dir: Path, files: dict, *names: str):
    """Writes ``files`` (name to text) and copies the playroutine's ``names`` to ``build_dir``."""
    for name in names:
        shutil.copy(ASM_DIR / name, build_dir / name)
    for name, text in files.items():
        (build_dir / name).write_text(text)


def simulate(build_dir: Path, inc: str, song: str, ticks: int) -> tuple:
    """``(frames, cycles)``: the registers after each of ``ticks`` ticks of the playroutine run
    on ``mos-sim``, and the cycles each tick took, ``jsr`` and ``rts`` included."""
    stage(build_dir, {"song.inc": inc + f"        .set SIM_TICKS, {ticks}\n", "song.s": song})
    stage(build_dir, {}, "player.s", "sim.s", "link.ld")
    run(
        build_dir,
        f"mos-common-clang -nostdlib -T link.ld -Wl,--defsym=__load={SIM_LOAD}"
        " -Wl,--defsym=_start=sim -o sim.bin player.s song.s sim.s"
        " && llvm-nm sim.bin.elf > sim.nm",
    )
    image = (build_dir / "sim.bin").read_bytes()
    symbols = dict(
        (parts[2], int(parts[0], 16))
        for parts in (line.split() for line in (build_dir / "sim.nm").read_text().splitlines())
        if len(parts) == 3
    )
    blocks = struct.pack("<HH", SIM_LOAD, len(image)) + image
    blocks += struct.pack("<HHH", 0xFFFC, 2, symbols["sim"])
    (build_dir / "sim.img").write_bytes(blocks)
    out = run(build_dir, "mos-sim sim.img")
    rows = [[int(x, 16) for x in line.split()] for line in out.stdout.splitlines() if line]
    table = np.array(rows, dtype=np.int64).reshape(-1, 27)
    return table[:, 2:].astype(np.uint8), (table[:, 1] - table[:, 0]) % (1 << 24)
