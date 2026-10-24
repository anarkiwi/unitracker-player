"""Caps numpy's thread pools so each xdist worker starts on hosts with many cores; the llvm-mos
toolchain and the HVSC fixture songs as checked-in canonical documents."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

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
