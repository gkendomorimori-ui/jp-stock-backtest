"""Data archives: pack -> record -> verify -> restore."""

import json
import zipfile
from pathlib import Path

import pytest

from src.data.archive import ArchiveError, pack, restore


def make_tree(root: Path) -> None:
    for rel, text in {
        "data/raw/jquants/bars_daily/2026-01-05.json.gz": "a",
        "data/raw/jquants/master/2026-01-05.json.gz": "b",
        "data/processed/jquants/bars.parquet": "c",
        "data/processed/jquants/manifest.json": '{"bar_rows": 1}',
    }.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def test_pack_and_restore_roundtrip(tmp_path: Path) -> None:
    src, dst = tmp_path / "src", tmp_path / "dst"
    make_tree(src)
    record = pack(src, tmp_path / "archive", "x")
    assert record["archives"]["raw"]["members"] == 2
    assert record["processed_manifest"] == {"bar_rows": 1}
    json.dumps(record)  # serializable
    for a in record["archives"].values():
        restore(tmp_path / "archive" / a["file"], record, dst)
    assert (dst / "data/processed/jquants/bars.parquet").read_text() == "c"
    # existing files are not overwritten without the flag
    with pytest.raises(ArchiveError, match="already exists"):
        restore(tmp_path / "archive" / "jquants_raw_x.zip", record, dst)
    restore(tmp_path / "archive" / "jquants_raw_x.zip", record, dst, overwrite=True)


def test_hash_mismatch_extracts_nothing(tmp_path: Path) -> None:
    src = tmp_path / "src"
    make_tree(src)
    record = pack(src, tmp_path / "archive", "x")
    z = tmp_path / "archive" / "jquants_raw_x.zip"
    z.write_bytes(z.read_bytes() + b"tampered")
    with pytest.raises(ArchiveError, match="SHA-256"):
        restore(z, record, tmp_path / "dst")
    assert not (tmp_path / "dst").exists()


def test_unsafe_member_is_refused(tmp_path: Path) -> None:
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("../outside.txt", "x")
    from src.data.archive import sha256_file

    record = {"archives": {"raw": {"file": "evil.zip", "sha256": sha256_file(z), "members": 1}}}
    with pytest.raises(ArchiveError, match="unsafe"):
        restore(z, record, tmp_path / "dst")


def test_empty_source_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ArchiveError):
        pack(tmp_path, tmp_path / "archive", "x")
