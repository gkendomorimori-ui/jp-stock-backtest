"""Pack the downloaded / processed data into zip archives with SHA-256 records.

The archives contain J-Quants data and must stay PRIVATE (J-Quants terms prohibit
distributing or sharing the data itself): keep them in your own storage only, never in git
or in a shared folder. What goes into git is the small JSON record (sizes, hashes, file
counts, the processed manifest) so a restored copy can be verified.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

ARCHIVES: dict[str, str] = {
    "raw": "data/raw/jquants",
    "processed": "data/processed/jquants",
}


class ArchiveError(Exception):
    """An archive is missing, corrupt or does not match its record."""


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """Hex SHA-256 of a file (streamed)."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def pack(project_root: Path, out_dir: Path, label: str) -> dict[str, Any]:
    """Zip each data directory into ``out_dir`` and return the record (not written here).

    Member paths are relative to the project root (e.g. ``data/raw/jquants/...``) and sorted,
    so the same files always produce the same member list.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "label": label,
        "note": "J-Quants data: keep the zip files private (no git, no shared links).",
        "archives": {},
    }
    for name, rel in ARCHIVES.items():
        src = project_root / rel
        files = sorted(p for p in src.rglob("*") if p.is_file())
        if not files:
            raise ArchiveError(f"{rel} has no files")
        zpath = out_dir / f"jquants_{name}_{label}.zip"
        with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for f in files:
                z.write(f, f.relative_to(project_root).as_posix())
        record["archives"][name] = {
            "file": zpath.name,
            "bytes": zpath.stat().st_size,
            "sha256": sha256_file(zpath),
            "members": len(files),
            "source_bytes": sum(f.stat().st_size for f in files),
        }
    manifest = project_root / ARCHIVES["processed"] / "manifest.json"
    if manifest.exists():
        record["processed_manifest"] = json.loads(manifest.read_text(encoding="utf-8"))
    return record


def restore(
    zip_path: Path, record: dict[str, Any], project_root: Path, overwrite: bool = False
) -> dict[str, Any]:
    """Verify ``zip_path`` against ``record`` and extract it into ``project_root``.

    Raises:
        ArchiveError: Unknown file, hash or member-count mismatch, unsafe member path, or
            existing files without ``overwrite``.
    """
    entry = next((a for a in record["archives"].values() if a["file"] == zip_path.name), None)
    if entry is None:
        raise ArchiveError(f"{zip_path.name} is not in the record")
    digest = sha256_file(zip_path)
    if digest != entry["sha256"]:
        raise ArchiveError(f"SHA-256 mismatch for {zip_path.name}: {digest} != {entry['sha256']}")
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        if len(names) != entry["members"]:
            raise ArchiveError(f"{len(names)} members, record says {entry['members']}")
        root = project_root.resolve()
        for n in names:
            target = (project_root / n).resolve()
            if not n.startswith("data/") or root not in target.parents:
                raise ArchiveError(f"unsafe member path {n!r}")
            if target.exists() and not overwrite:
                raise ArchiveError(f"{n} already exists (use --overwrite)")
        z.extractall(project_root, members=names)
    return {"file": zip_path.name, "sha256": digest, "members": len(names)}
