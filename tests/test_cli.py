"""``unitracker-play`` writes the frames play() gives."""

import numpy as np
import pytest
from song import doc

from unitracker_player import play
from unitracker_player.cli import main

HEADER = ",".join(f"d4{r:02x}" for r in range(25))


@pytest.fixture(name="song")
def fixture_song(tmp_path):
    path = tmp_path / "song.yaml"
    path.write_text(doc(ending=0), encoding="utf-8")
    return path


def _csv(text: str) -> np.ndarray:
    head, *rows = text.splitlines()
    assert head == HEADER
    return np.array([[int(x) for x in r.split(",")] for r in rows], dtype=np.uint8)


def test_npz_and_csv_files_hold_the_played_frames(song, tmp_path):
    """--out .npz stores array `frames`; .csv has the d400..d418 header then a row per tick."""
    expected = play(song.read_text(encoding="utf-8"), 30)
    assert main([str(song), "--ticks", "30", "--out", str(tmp_path / "f.npz")]) == 0
    assert np.array_equal(np.load(tmp_path / "f.npz")["frames"], expected)
    assert main([str(song), "--ticks", "30", "--out", str(tmp_path / "f.csv")]) == 0
    assert np.array_equal(_csv((tmp_path / "f.csv").read_text(encoding="utf-8")), expected)


def test_csv_goes_to_stdout_without_out(tmp_path, capsys):
    """Without --out an ending song is written whole to stdout."""
    path = tmp_path / "end.yaml"
    path.write_text(doc(), encoding="utf-8")
    assert main([str(path)]) == 0
    frames = _csv(capsys.readouterr().out)
    assert frames.shape == (24, 25) and np.array_equal(frames, play(doc()))


@pytest.mark.parametrize("args", [["--out", "f.wav"], []])
def test_bad_arguments_exit_with_usage(song, args):
    """An unknown --out suffix, or a looping song without --ticks, is a usage error."""
    with pytest.raises(SystemExit) as exc:
        main([str(song), *args])
    assert exc.value.code == 2
