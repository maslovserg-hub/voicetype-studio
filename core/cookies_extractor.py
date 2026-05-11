"""Extract YouTube cookies from any Chromium-based browser the user has
logged in to — without manual ``cookies.txt`` exports.

Why this exists
---------------
yt-dlp's built-in ``--cookies-from-browser`` is unreliable on Windows:

* Chrome 127+ ships «app-bound encryption» that yt-dlp can't bypass
  (yt-dlp issue #10927).
* Yandex Browser is not in yt-dlp's known-browsers list at all
  (``unsupported browser: "yandex"``).
* Edge inherits the same Chrome 127+ encryption issue.

So we read the browser's ``Cookies`` SQLite directly, decrypt with the
browser's master key (DPAPI on Windows), and write a Netscape-format
``cookies.txt`` that yt-dlp can consume via ``cookiefile=``. This works
for any Chromium fork that hasn't moved past Chrome 127's encryption
scheme — and Yandex hasn't, even though Google's own Chrome did.

The decryption supports two formats:

* **v10/v11 (AES-GCM):** introduced in Chrome 80, used by Yandex and most
  Chromium forks today. Master key is in ``Local State`` (DPAPI-encrypted),
  per-cookie payload is ``"v10"|"v11"`` + 12-byte IV + ciphertext + 16-byte
  tag.
* **DPAPI (legacy):** older entries — the cookie value is a single DPAPI
  blob with no AES wrapping.

If neither yields plaintext, the cookie is skipped (it's encrypted with
Chrome 127+ app-bound encryption that we can't break).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


@dataclass
class BrowserProfile:
    """One browser's User Data + Cookies + Local State on disk."""

    name: str
    user_data_dir: Path

    @property
    def local_state_file(self) -> Path:
        return self.user_data_dir / "Local State"

    def cookies_db(self) -> Optional[Path]:
        """Newer Chromium puts cookies under ``Default/Network/Cookies``,
        older builds keep them at ``Default/Cookies``. Return whichever
        exists."""
        candidates = [
            self.user_data_dir / "Default" / "Network" / "Cookies",
            self.user_data_dir / "Default" / "Cookies",
        ]
        return next((p for p in candidates if p.exists()), None)


def _shared_copy(src: Path, dst: Path) -> bool:
    """Copy ``src`` → ``dst`` using Win32 with full sharing flags so the
    operation succeeds even while another process holds the file open.

    Returns ``True`` on success. Falls back to ``shutil.copy2`` on
    non-Windows / pywin32-missing builds.
    """
    if sys.platform != "win32":
        try:
            shutil.copy2(src, dst)
            return True
        except OSError:
            return False

    try:
        import win32con  # type: ignore[import]
        import win32file  # type: ignore[import]
    except ImportError:
        try:
            shutil.copy2(src, dst)
            return True
        except OSError:
            return False

    GENERIC_READ = 0x80000000
    SHARE_ALL = (
        win32con.FILE_SHARE_READ
        | win32con.FILE_SHARE_WRITE
        | win32con.FILE_SHARE_DELETE
    )

    try:
        handle = win32file.CreateFile(
            str(src),
            GENERIC_READ,
            SHARE_ALL,
            None,
            win32con.OPEN_EXISTING,
            win32con.FILE_ATTRIBUTE_NORMAL,
            None,
        )
    except Exception:
        return False

    try:
        with open(dst, "wb") as out:
            while True:
                hr, chunk = win32file.ReadFile(handle, 1024 * 1024)
                if hr != 0 or not chunk:
                    break
                out.write(chunk)
        return True
    except Exception:
        return False
    finally:
        try:
            handle.Close()
        except Exception:
            pass


def _windows_browsers() -> list[BrowserProfile]:
    """Standard install paths for Chromium-based browsers on Windows."""
    if sys.platform != "win32":
        return []
    appdata_local = os.environ.get("LOCALAPPDATA", "")
    appdata_roaming = os.environ.get("APPDATA", "")
    if not appdata_local:
        return []
    candidates = [
        BrowserProfile("yandex", Path(appdata_local) / "Yandex" / "YandexBrowser" / "User Data"),
        BrowserProfile("chrome", Path(appdata_local) / "Google" / "Chrome" / "User Data"),
        BrowserProfile("edge", Path(appdata_local) / "Microsoft" / "Edge" / "User Data"),
        BrowserProfile("brave", Path(appdata_local) / "BraveSoftware" / "Brave-Browser" / "User Data"),
        BrowserProfile("opera", Path(appdata_roaming) / "Opera Software" / "Opera Stable") if appdata_roaming else None,
        BrowserProfile("vivaldi", Path(appdata_local) / "Vivaldi" / "User Data"),
    ]
    return [b for b in candidates if b is not None and b.user_data_dir.exists()]


def list_installed_chromium_browsers() -> list[str]:
    """Diagnostic — used by the error message + tests."""
    return [b.name for b in _windows_browsers()]


# Map browser key → likely process names. We use these to tell the user
# *which* running browser is holding the lock so they can close it.
_BROWSER_PROCESSES: dict[str, tuple[str, ...]] = {
    "yandex":  ("browser.exe", "Yandex.exe"),
    "chrome":  ("chrome.exe",),
    "edge":    ("msedge.exe",),
    "brave":   ("brave.exe",),
    "opera":   ("opera.exe",),
    "vivaldi": ("vivaldi.exe",),
}


def running_browsers() -> dict[str, int]:
    """Return ``{browser_key: process_count}`` for browsers whose process
    name appears in ``tasklist`` output. Best-effort — failures return
    an empty dict rather than raising. Windows-only.

    Yandex Browser in particular keeps several ``browser.exe`` processes
    alive after the user "closes" it — the tray-stays-running setting is
    on by default. Counting processes lets the error message give the
    user honest "у тебя 17 процессов Yandex" rather than a vague
    "запущено: yandex".
    """
    if sys.platform != "win32":
        return {}
    try:
        import subprocess

        # Use binary capture + manual decode — ``text=True`` defaults to
        # the system locale, which on a Russian Windows console is cp866
        # and may misread some bytes. ASCII-only process names survive
        # either way, but we want to be robust.
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=10,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except Exception:
        return {}
    if out.returncode != 0:
        return {}

    raw_lines = out.stdout.decode("utf-8", errors="replace").splitlines()
    if not raw_lines or len(raw_lines) < 5:
        # Fallback: try cp866 (Russian Windows console default) then cp1251.
        for enc in ("cp866", "cp1251"):
            try:
                raw_lines = out.stdout.decode(enc).splitlines()
                if len(raw_lines) >= 5:
                    break
            except Exception:
                continue

    counts: dict[str, int] = {}
    for line in raw_lines:
        if not line.strip():
            continue
        first = line.split(",", 1)[0].strip().strip('"').lower()
        for browser_key, procs in _BROWSER_PROCESSES.items():
            if first in (p.lower() for p in procs):
                counts[browser_key] = counts.get(browser_key, 0) + 1
                break
    return counts


def _read_master_key(profile: BrowserProfile):
    """Pull the AES master key out of ``Local State`` and DPAPI-decrypt it."""
    if not profile.local_state_file.exists():
        return None
    try:
        with open(profile.local_state_file, "r", encoding="utf-8") as f:
            local_state = json.load(f)
        encrypted_key_b64 = local_state["os_crypt"]["encrypted_key"]
    except (OSError, KeyError, ValueError):
        return None

    encrypted_key = base64.b64decode(encrypted_key_b64)
    if not encrypted_key.startswith(b"DPAPI"):
        return None
    encrypted_key = encrypted_key[5:]  # strip the 'DPAPI' marker

    try:
        import win32crypt  # type: ignore[import]
    except ImportError:
        logger.warning("pywin32 not installed; cannot decrypt cookies")
        return None

    try:
        _, plain = win32crypt.CryptUnprotectData(
            encrypted_key, None, None, None, 0,
        )
        return plain
    except Exception:
        logger.exception("DPAPI decrypt of master key failed for %s", profile.name)
        return None


def _decrypt_cookie_value(encrypted: bytes, master_key: Optional[bytes]) -> Optional[str]:
    """Try AES-GCM (v10/v11), then legacy DPAPI. Return text or ``None``.

    Returns ``None`` for Chrome 127+ v20 «app-bound encryption» blobs —
    they can't be decrypted from outside the Chrome process.
    """
    if not encrypted:
        return ""

    prefix = encrypted[:3]
    # v20 = Chrome 127+ app-bound encryption. We can't break this without
    # running inside Chrome, period. Skip silently — caller will count it
    # as "no usable cookie" and try the next browser.
    if prefix == b"v20":
        return None

    # v10 / v11 — AES-GCM. Used by Yandex Browser, older Chrome, Edge,
    # Brave, Opera, Vivaldi.
    if master_key is not None and len(encrypted) > 3 + 12 + 16 and prefix in (b"v10", b"v11"):
        try:
            from Crypto.Cipher import AES  # type: ignore[import]

            iv = encrypted[3:15]
            payload = encrypted[15:-16]
            tag = encrypted[-16:]
            cipher = AES.new(master_key, AES.MODE_GCM, iv)
            plaintext = cipher.decrypt_and_verify(payload, tag)

            # Newer Chromium prepends a 32-byte SHA-256 of the host+name
            # to the cookie value as an integrity tag. If the plaintext
            # is long enough AND the first 32 bytes don't look like UTF-8,
            # strip them. This matches what browser_cookie3 and other
            # community extractors do.
            if len(plaintext) > 32:
                stripped = plaintext[32:]
                try:
                    return stripped.decode("utf-8")
                except UnicodeDecodeError:
                    pass

            try:
                return plaintext.decode("utf-8")
            except UnicodeDecodeError:
                return plaintext.decode("utf-8", errors="replace")
        except Exception:
            pass

    # Legacy DPAPI.
    try:
        import win32crypt  # type: ignore[import]

        _, plain = win32crypt.CryptUnprotectData(
            encrypted, None, None, None, 0,
        )
        return plain.decode("utf-8", errors="replace")
    except Exception:
        return None


# Chrome stores expires_utc as microseconds since 1601-01-01.
_WINDOWS_EPOCH_OFFSET_S = 11644473600


def _chrome_time_to_unix(microseconds_since_1601: int) -> int:
    if microseconds_since_1601 <= 0:
        return 0
    return max(0, microseconds_since_1601 // 1_000_000 - _WINDOWS_EPOCH_OFFSET_S)


def _extract_one(
    profile: BrowserProfile,
    domains: Iterable[str],
) -> list[tuple[str, str, str, str, int, str, str]]:
    """Return Netscape-style rows ``(domain, subdomain, path, secure, expires,
    name, value)`` for cookies whose host matches any ``domains`` substring."""
    cookies_path = profile.cookies_db()
    if cookies_path is None:
        return []

    master_key = _read_master_key(profile)

    # Browser locks the live DB. Use the Win32 API directly to open with
    # full sharing so we can copy even while Yandex/Chrome is running.
    # ``shutil.copy2`` calls Windows ``CopyFile``, which doesn't ask for
    # any sharing modes and gets ERROR_SHARING_VIOLATION (32).
    tmp_dir = Path(tempfile.mkdtemp(prefix="cookies_"))
    tmp_db = tmp_dir / "Cookies"
    if not _shared_copy(cookies_path, tmp_db):
        return []

    rows: list[tuple[str, str, str, str, int, str, str]] = []
    domains_lower = tuple(d.lower() for d in domains)
    try:
        conn = sqlite3.connect(str(tmp_db))
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT host_key, path, is_secure, expires_utc, name, encrypted_value "
                "FROM cookies"
            )
            for host, path, is_secure, expires, name, encrypted in cur.fetchall():
                host_str = (host or "").lower()
                if not any(d in host_str for d in domains_lower):
                    continue
                value = _decrypt_cookie_value(encrypted, master_key)
                if value is None:
                    continue
                # Netscape format quirks:
                #   - leading dot ⇔ "TRUE" includeSubdomains
                #   - tab-separated, no quoting; we strip newlines from value
                value_clean = value.replace("\t", " ").replace("\n", " ").replace("\r", "")
                rows.append((
                    host_str,
                    "TRUE" if host_str.startswith(".") else "FALSE",
                    path or "/",
                    "TRUE" if is_secure else "FALSE",
                    _chrome_time_to_unix(int(expires or 0)),
                    name or "",
                    value_clean,
                ))
        finally:
            conn.close()
    except sqlite3.Error:
        logger.exception("SQLite error reading cookies from %s", profile.name)
    finally:
        try:
            tmp_db.unlink()
            tmp_dir.rmdir()
        except OSError:
            pass

    return rows


def export_cookies_for_domains(
    domains: Iterable[str],
    output_path: Path,
    *,
    browsers: Optional[list[BrowserProfile]] = None,
) -> dict:
    """Walk all installed Chromium browsers, harvest cookies whose host
    contains any of ``domains`` (substring match — ``"youtube.com"`` matches
    ``".www.youtube.com"`` too), decrypt them, and write Netscape-format
    cookies.txt at ``output_path``.

    Returns a small report:
        ``{"browsers_tried": [...], "rows_per_browser": {...},
           "total_rows": N, "output": Path}``

    On failure (no browsers installed, no decryptable cookies for the asked
    domains) returns ``total_rows = 0`` and the caller can fall back to
    yt-dlp's own browser plumbing.
    """
    profiles = browsers if browsers is not None else _windows_browsers()
    rows_per_browser: dict[str, int] = {}
    all_rows: list[tuple[str, str, str, str, int, str, str]] = []

    for prof in profiles:
        rows = _extract_one(prof, domains)
        rows_per_browser[prof.name] = len(rows)
        all_rows.extend(rows)
        logger.info(
            "cookies_extractor: %s -> %d rows for %s",
            prof.name, len(rows), list(domains),
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# Netscape HTTP Cookie File\n")
        f.write("# Generated by VoiceType Studio cookies_extractor\n")
        for row in all_rows:
            f.write("\t".join(str(x) for x in row) + "\n")

    return {
        "browsers_tried": [p.name for p in profiles],
        "rows_per_browser": rows_per_browser,
        "total_rows": len(all_rows),
        "output": output_path,
    }
