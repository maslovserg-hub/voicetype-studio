"""Tests for ``core.cookies_extractor``.

We don't touch real browser DBs (their state is unstable and platform-
specific). Instead we exercise the pure helpers and the v10/v11 AES-GCM
decryption with synthesised data.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

from core.cookies_extractor import (
    BrowserProfile,
    _chrome_time_to_unix,
    _decrypt_cookie_value,
    _shared_copy,
    export_cookies_for_domains,
    list_installed_chromium_browsers,
    running_browsers,
)


# ---- pure helpers --------------------------------------------------------


def test_chrome_time_to_unix() -> None:
    # Chrome stores microseconds since 1601-01-01.
    # The offset between 1601 and the Unix epoch is 11_644_473_600 seconds.
    # ``(target_unix + 11_644_473_600) * 1_000_000`` is the Chrome value.
    chrome_2024 = (1_704_067_200 + 11_644_473_600) * 1_000_000
    assert _chrome_time_to_unix(chrome_2024) == 1_704_067_200
    assert _chrome_time_to_unix(0) == 0
    assert _chrome_time_to_unix(-1) == 0


def test_decrypt_v20_returns_none_silently() -> None:
    """Chrome 127+ app-bound encryption can't be cracked from outside Chrome.
    The extractor must skip those cookies, not crash."""
    fake_v20 = b"v20" + b"\x00" * 20
    assert _decrypt_cookie_value(fake_v20, master_key=b"\x00" * 32) is None


def test_decrypt_empty_returns_empty_string() -> None:
    assert _decrypt_cookie_value(b"", master_key=b"\x00" * 32) == ""


def test_decrypt_v10_round_trip() -> None:
    """Verify our v10 path actually works given a hand-rolled blob."""
    from Crypto.Cipher import AES
    from Crypto.Random import get_random_bytes

    master_key = get_random_bytes(32)
    iv = get_random_bytes(12)
    plain = "test=value; path=/".encode("utf-8")

    cipher = AES.new(master_key, AES.MODE_GCM, iv)
    ciphertext, tag = cipher.encrypt_and_digest(plain)
    blob = b"v10" + iv + ciphertext + tag

    assert _decrypt_cookie_value(blob, master_key) == "test=value; path=/"


def test_decrypt_v11_round_trip() -> None:
    """Same as above for the v11 prefix Yandex/recent Chromium use."""
    from Crypto.Cipher import AES
    from Crypto.Random import get_random_bytes

    master_key = get_random_bytes(32)
    iv = get_random_bytes(12)
    plain = b"abc"
    cipher = AES.new(master_key, AES.MODE_GCM, iv)
    ciphertext, tag = cipher.encrypt_and_digest(plain)
    blob = b"v11" + iv + ciphertext + tag
    assert _decrypt_cookie_value(blob, master_key) == "abc"


# ---- list_installed / running --------------------------------------------


def test_list_installed_returns_list() -> None:
    """No assertion on contents (depends on the dev's machine) — just shape."""
    result = list_installed_chromium_browsers()
    assert isinstance(result, list)
    assert all(isinstance(x, str) for x in result)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only check")
def test_running_browsers_returns_dict_with_counts() -> None:
    """``running_browsers()`` returns ``{browser_key: process_count}``.
    Exact contents depend on the dev's machine; we only check the shape."""
    result = running_browsers()
    assert isinstance(result, dict)
    for k, v in result.items():
        assert isinstance(k, str)
        assert isinstance(v, int) and v > 0


# ---- export_cookies_for_domains end-to-end ------------------------------


def _build_fake_chromium_profile(tmp_path: Path) -> BrowserProfile:
    """Synthesize a working Chromium-style profile with one v10 YouTube cookie
    and a Local State pointing at a dummy DPAPI-protected master key.

    We can't *actually* DPAPI-protect a real key in a portable test, so we
    skip if pywin32 isn't available and lean on a monkeypatch in the caller.
    """
    user_data = tmp_path / "User Data"
    profile_dir = user_data / "Default" / "Network"
    profile_dir.mkdir(parents=True)
    cookies_db = profile_dir / "Cookies"

    # The simplest schema Chromium uses for cookies. We only populate the
    # columns ``_extract_one`` reads.
    conn = sqlite3.connect(str(cookies_db))
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE cookies ("
        " host_key TEXT, path TEXT, is_secure INTEGER, expires_utc INTEGER,"
        " name TEXT, encrypted_value BLOB)"
    )
    conn.commit()
    conn.close()

    return BrowserProfile(name="fake", user_data_dir=user_data)


def test_export_writes_netscape_format(tmp_path: Path, monkeypatch) -> None:
    from core import cookies_extractor as mod
    from Crypto.Cipher import AES
    from Crypto.Random import get_random_bytes

    profile = _build_fake_chromium_profile(tmp_path)
    master_key = get_random_bytes(32)

    # Add one v10-encrypted cookie for youtube.com.
    iv = get_random_bytes(12)
    plain = b"abc123"
    cipher = AES.new(master_key, AES.MODE_GCM, iv)
    ciphertext, tag = cipher.encrypt_and_digest(plain)
    blob = b"v10" + iv + ciphertext + tag

    db = profile.cookies_db()
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO cookies (host_key, path, is_secure, expires_utc, name, encrypted_value) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (".youtube.com", "/", 1, 13_338_403_200_000_000, "SID", blob),
    )
    conn.execute(
        "INSERT INTO cookies (host_key, path, is_secure, expires_utc, name, encrypted_value) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (".unrelated.com", "/", 1, 0, "X", b""),
    )
    conn.commit()
    conn.close()

    # Inject our master key by monkeypatching ``_read_master_key``.
    monkeypatch.setattr(mod, "_read_master_key", lambda p: master_key)

    out = tmp_path / "cookies.txt"
    report = mod.export_cookies_for_domains(
        ("youtube.com",), out, browsers=[profile],
    )
    assert report["total_rows"] == 1
    text = out.read_text(encoding="utf-8")
    assert "# Netscape HTTP Cookie File" in text
    assert ".youtube.com" in text
    assert "abc123" in text
    assert "unrelated.com" not in text


# ---- _shared_copy --------------------------------------------------------


def test_shared_copy_unlocked_file(tmp_path: Path) -> None:
    """Sanity — copying a regular file works on every platform."""
    src = tmp_path / "src.bin"
    src.write_bytes(b"abc" * 100)
    dst = tmp_path / "dst.bin"
    assert _shared_copy(src, dst) is True
    assert dst.read_bytes() == src.read_bytes()
