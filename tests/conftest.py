"""Caps numpy's thread pools so each xdist worker starts on hosts with many cores; the llvm-mos
toolchain and the HVSC fixture songs, lifted by unitracker into pysidtracker's cache."""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from unitracker_grammar.document import printed, read

for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_var, "2")

FIXTURES = {
    "crosseyed": "MUSICIANS/I/Ilkke/Crosseyed.sid",
    "cocktail_hour": "MUSICIANS/E/Et1999cc/Cocktail_Hour.sid",
    "2600_hidden_part": "MUSICIANS/K/Klegg/2600_hidden_part.sid",
}


def _docker() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


@pytest.fixture(scope="session")
def toolchain():
    """Skips without llvm-mos: mos-common-clang on PATH, or docker for the SDK's image."""
    if not (shutil.which("mos-common-clang") or _docker()):
        pytest.skip("llvm-mos unavailable: no mos-common-clang and no docker")


def lifted(relpath: str) -> str:
    """The generic document of HVSC tune ``relpath``, lifted by unitracker and cached by the
    tune's md5 in pysidtracker's cache, in its one canonical text."""
    testing = pytest.importorskip("pysidtracker.testing")
    backends = pytest.importorskip("unitracker.lift.backends")
    path = testing.resolve_tune(relpath)
    if path is None:
        pytest.skip(f"{relpath}: HVSC tune unavailable")
    raw = Path(path).read_bytes()
    cached = testing.default_cache() / "unitracker" / f"{hashlib.md5(raw).hexdigest()}.yaml"
    if not cached.is_file():
        lift = backends.lifter(raw)
        try:
            lift.run()
        except pytest.importorskip("unitracker.lift.base").LiftError as exc:
            pytest.skip(f"{relpath}: the lifter refuses it: {exc}")
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(lift.document, encoding="utf-8")
    return printed(read(cached.read_text(encoding="utf-8")))
