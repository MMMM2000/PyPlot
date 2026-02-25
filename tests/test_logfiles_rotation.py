from __future__ import annotations

from pathlib import Path

from plotting.shared.logfiles import append_text_with_rotation, open_rotating_text_log


def test_append_text_with_rotation_rotates_when_size_limit_hit(tmp_path: Path) -> None:
    log_path = tmp_path / "message_log.txt"
    log_path.write_text("A" * 20, encoding="utf-8")

    append_text_with_rotation(log_path, "next\n", max_bytes=10, backup_count=3)

    rotated = tmp_path / "message_log.txt.1"
    assert rotated.exists()
    assert rotated.read_text(encoding="utf-8") == "A" * 20
    assert log_path.read_text(encoding="utf-8") == "next\n"


def test_open_rotating_text_log_keeps_backup_window(tmp_path: Path) -> None:
    log_path = tmp_path / "crash_log.txt"
    for index in range(1, 5):
        (tmp_path / f"crash_log.txt.{index}").write_text(str(index), encoding="utf-8")
    log_path.write_text("seed", encoding="utf-8")

    with open_rotating_text_log(log_path, max_bytes=1, backup_count=3) as handle:
        handle.write("x\n")

    # backup_count=3 means previous .4 is dropped, and existing backups shift up.
    assert not (tmp_path / "crash_log.txt.4").exists()
    assert (tmp_path / "crash_log.txt.3").read_text(encoding="utf-8") == "2"
    assert (tmp_path / "crash_log.txt.2").read_text(encoding="utf-8") == "1"
    assert (tmp_path / "crash_log.txt.1").read_text(encoding="utf-8") == "seed"
    assert log_path.read_text(encoding="utf-8") == "x\n"

