from conftest import FILES

from yadisk_dl.api import normalize
from yadisk_dl.cli import main


def test_normalize():
    assert normalize("/") == "disk:/"
    assert normalize("Photos") == "disk:/Photos"
    assert normalize("disk:/Photos/") == "disk:/Photos"
    assert normalize("  /Photos/2026 ") == "disk:/Photos/2026"


def test_downloads_folder_with_subfolders(api, tmp_path):
    out = tmp_path / "out"
    assert main(["/Photos", "-o", str(out), "--yes"]) == 0
    assert (out / "a.jpg").read_bytes() == FILES["disk:/Photos/a.jpg"]
    assert (out / "b.jpg").read_bytes() == FILES["disk:/Photos/b.jpg"]
    assert (out / "2026" / "c.jpg").read_bytes() == FILES["disk:/Photos/2026/c.jpg"]
    assert not list(out.rglob("*.part"))


def test_no_subfolders(api, tmp_path):
    out = tmp_path / "out"
    assert main(["/Photos", "-o", str(out), "--no-subfolders", "--yes"]) == 0
    assert (out / "a.jpg").exists()
    assert not (out / "2026").exists()


def test_whole_disk_keeps_structure(api, tmp_path):
    out = tmp_path / "out"
    assert main(["/", "-o", str(out), "--yes"]) == 0
    assert (out / "Docs" / "readme.txt").read_bytes() == b"hello"
    assert (out / "Photos" / "2026" / "c.jpg").exists()


def test_single_file(api, tmp_path):
    out = tmp_path / "out"
    assert main(["/Docs/readme.txt", "-o", str(out), "--yes"]) == 0
    assert (out / "readme.txt").read_bytes() == b"hello"


def test_second_run_skips_finished_files(api, tmp_path):
    out = tmp_path / "out"
    main(["/Photos", "-o", str(out), "--yes"])
    first = len(api.hits)
    assert first == 3
    assert main(["/Photos", "-o", str(out), "--yes"]) == 0
    assert len(api.hits) == first


def test_truncated_file_is_downloaded_again(api, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.jpg").write_bytes(b"A" * 10)
    assert main(["/Photos", "-o", str(out), "--no-subfolders", "--yes"]) == 0
    assert (out / "a.jpg").read_bytes() == FILES["disk:/Photos/a.jpg"]


def test_wrong_token(api, tmp_path, monkeypatch):
    monkeypatch.setenv("YADISK_TOKEN", "nope")
    assert main(["/Photos", "-o", str(tmp_path / "out"), "--yes"]) == 1
    assert not (tmp_path / "out" / "a.jpg").exists()


def test_missing_path(api, tmp_path):
    assert main(["/Nothing", "-o", str(tmp_path / "out"), "--yes"]) == 1


def test_requires_path_and_out_without_terminal(api):
    assert main([]) == 2
