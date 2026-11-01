"""Caps numpy's thread pools so each xdist worker starts on hosts with many cores; the llvm-mos
toolchain and the HVSC fixture songs as checked-in canonical documents."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from unitracker_player import native
from unitracker_player import player as player_module
from unitracker_player.compiler import REGISTERS
from unitracker_player.sequencer import PlayError

for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_var, "2")

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURES = sorted(p.stem for p in FIXTURE_DIR.glob("*.yaml"))


def _docker() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


@pytest.fixture(scope="session")
def toolchain():
    """Skips without llvm-mos: mos-common-clang on PATH, or docker for the SDK's image."""
    if not (shutil.which("mos-common-clang") or _docker()):
        pytest.skip("llvm-mos unavailable: no mos-common-clang and no docker")


def lifted(name: str) -> str:
    """The checked-in canonical document of fixture ``name``, lifted from its HVSC tune by
    unitracker's direct lifters."""
    return (FIXTURE_DIR / f"{name}.yaml").read_text(encoding="utf-8")


class Agreeing:
    """A native player that also plays its song by the Python call machine and fails on the
    first difference, so every document the tests play is a differential case."""

    def __init__(self, song, inner):
        self.inner, self.python = inner, player_module.Player(song)

    def run(self, frames: int) -> bytes:
        """The native rows of up to ``frames`` frames, equal to the Python player's."""
        rows = self.inner.run(frames)
        want = bytearray()
        try:
            for _ in range(frames):
                regs = self.python.step()
                if regs is None:
                    break
                want += bytes(regs)
        except PlayError as exc:
            raise AssertionError(f"native played what the Python player refuses: {exc}") from exc
        if bytes(want) != rows:
            at = next((i for i, (a, b) in enumerate(zip(want, rows)) if a != b), None)
            where = "length" if at is None else f"frame {at // REGISTERS} register {at % REGISTERS}"
            raise AssertionError(f"the native player differs from the Python player: {where}")
        return rows


@pytest.fixture(autouse=True)
def agree(monkeypatch):
    """Plays every document by both call machines wherever the native one is built."""
    if native.enabled():
        make = player_module.native_player

        def agreeing(song):
            inner = make(song)
            return None if inner is None else Agreeing(song, inner)

        monkeypatch.setattr(player_module, "native_player", agreeing)
