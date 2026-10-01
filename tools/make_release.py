"""Pack ``dist/VoiceTypeStudio`` into the release assets.

    python tools/make_release.py

Writes, next to the repo root:

- ``VoiceTypeStudio_release.zip`` — the full install (~220 MB);
- ``VoiceTypeStudio_update.zip`` — only the files that change with the
  app code (~50 MB);
- ``deps.txt`` — fingerprint of everything else in ``_internal``.

``deps.txt`` also goes inside both zips, so every install knows its own
fingerprint. The update script compares it with the latest release's and
downloads the small zip when they match.
"""

from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

DIST = Path("dist/VoiceTypeStudio")
# Files rebuilt with the app code — shipped in the small zip, so they
# don't count towards the fingerprint.
APP_FILES = (
    "VoiceTypeStudio.exe",
    "_internal/base_library.zip",
    "_internal/tools/VoiceTypeStudio-Update.bat",
)
DEPS_NAME = "deps.txt"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def deps_fingerprint(root: Path) -> str:
    """One hash over every file in ``root`` except :data:`APP_FILES`."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel in APP_FILES or rel == DEPS_NAME:
            continue
        h.update(f"{rel}\0{_sha256(path)}\n".encode("utf-8"))
    return h.hexdigest()


def main() -> int:
    if not (DIST / "VoiceTypeStudio.exe").is_file():
        print(f"{DIST} not built — run pyinstaller VoiceTypeStudio.spec first")
        return 1

    print("fingerprinting _internal…")
    (DIST / DEPS_NAME).write_text(deps_fingerprint(DIST) + "\n", encoding="ascii")
    Path(DEPS_NAME).write_bytes((DIST / DEPS_NAME).read_bytes())

    # Flat layout: entries are relative to DIST, no VoiceTypeStudio\ level
    # (launcher.py and the update script unpack straight into the install).
    print("packing VoiceTypeStudio_update.zip…")
    with zipfile.ZipFile("VoiceTypeStudio_update.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for rel in (*APP_FILES, DEPS_NAME):
            z.write(DIST / rel, rel)

    print("packing VoiceTypeStudio_release.zip…")
    with zipfile.ZipFile("VoiceTypeStudio_release.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(p for p in DIST.rglob("*") if p.is_file()):
            z.write(path, path.relative_to(DIST).as_posix())

    for name in ("VoiceTypeStudio_release.zip", "VoiceTypeStudio_update.zip", DEPS_NAME):
        print(f"  {name}: {Path(name).stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
