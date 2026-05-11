import re
import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Callable, Optional, Tuple
from urllib.parse import urlparse, unquote

import aiohttp
import aiofiles
import yt_dlp

from .config import config
from .cookies_extractor import (
    export_cookies_for_domains,
    list_installed_chromium_browsers,
    running_browsers,
)

logger = logging.getLogger(__name__)


# Map URL host substrings to the cookie domains that auth them. The keys
# are matched as case-insensitive substrings of the URL host; the values
# are passed to ``cookies_extractor.export_cookies_for_domains`` which
# itself substring-matches against the cookie's host_key.
_DOMAIN_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("youtube.com",   ("youtube.com", "google.com")),
    ("youtu.be",      ("youtube.com", "google.com")),
    ("googlevideo",   ("youtube.com", "google.com")),
    ("rutube.ru",     ("rutube.ru",)),
    ("vk.com",        ("vk.com", "vkvideo.ru")),
    ("vk.ru",         ("vk.com", "vkvideo.ru")),
    ("vkvideo",       ("vk.com", "vkvideo.ru")),
    ("disk.yandex",   ("yandex.ru", "passport.yandex.ru")),
    ("yadi.sk",       ("yandex.ru", "passport.yandex.ru")),
    ("drive.google",  ("google.com",)),
)


def _domains_for_url(url: str) -> tuple[str, ...]:
    """Return the cookie domains relevant to ``url``. Empty tuple = no hint."""
    host = (urlparse(url).hostname or "").lower()
    for needle, domains in _DOMAIN_HINTS:
        if needle in host:
            return domains
    return ()


class Downloader:
    YANDEX_DISK_PATTERN = re.compile(
        r"(?:disk\.yandex\.(?:ru|com)|yadi\.sk)/[di]/[a-zA-Z0-9_-]+",
        re.IGNORECASE,
    )
    DIRECT_FILE_EXTS = {".mp3", ".mp4", ".wav", ".m4a", ".ogg", ".webm", ".mkv", ".flac", ".oga"}

    @classmethod
    async def download(
        cls,
        url: str,
        *,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> Tuple[Path, str]:
        """Download media from URL. Returns ``(file_path, source_type)``.

        ``progress_callback(percent, status_text)`` is invoked from the
        download worker thread (yt-dlp / aiohttp). Callers must marshal
        any UI updates back to the GUI thread themselves — for the
        Transcriptor window that's the ``queue.Queue`` drain loop.
        """
        if cls.YANDEX_DISK_PATTERN.search(url):
            return await cls._download_yandex_disk(url), "yandex_disk"

        parsed = urlparse(url)
        ext = Path(parsed.path).suffix.lower()

        if ext in cls.DIRECT_FILE_EXTS:
            return await cls._download_direct(url), "direct"

        return await cls._download_ytdlp(
            url, progress_callback=progress_callback,
        ), "ytdlp"

    @classmethod
    async def _download_yandex_disk(cls, url: str) -> Path:
        """Download from Yandex.Disk public link."""
        api_url = "https://cloud-api.yandex.net/v1/disk/public/resources/download"

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, params={"public_key": url}) as resp:
                if resp.status != 200:
                    raise ValueError(f"Failed to get Yandex.Disk download URL: {resp.status}")
                data = await resp.json()
                download_url = data.get("href")
                if not download_url:
                    raise ValueError("No download URL in Yandex.Disk response")

            filename = _extract_filename(download_url) or "yandex_file"
            file_path = config.temp_dir / filename

            async with session.get(download_url, timeout=aiohttp.ClientTimeout(total=config.download_timeout_s)) as resp:
                if resp.status != 200:
                    raise ValueError(f"Failed to download file: {resp.status}")
                async with aiofiles.open(file_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        await f.write(chunk)

        return file_path

    @classmethod
    async def _download_direct(cls, url: str) -> Path:
        """Download file from direct URL."""
        filename = _extract_filename(url) or "direct_file"
        file_path = config.temp_dir / filename

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=config.download_timeout_s)) as resp:
                if resp.status != 200:
                    raise ValueError(f"Failed to download file: {resp.status}")
                async with aiofiles.open(file_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        await f.write(chunk)

        return file_path

    # Browsers tried in order when YouTube/etc. demand a "not a bot" check.
    # Order matters — we want the ones that actually WORK first.
    #
    # firefox: doesn't use app-bound encryption — yt-dlp reads its cookies
    #     reliably. Best option if the user has logged in there.
    # brave / opera / vivaldi: also Chromium-based but commonly without
    #     the app-bound encryption headache.
    # chrome / edge: yt-dlp issue #10927 — Chrome 127+ apps cookies with
    #     a system encryption that yt-dlp can't decrypt from outside the
    #     Chrome process. They're tried last as a long shot for users on
    #     older Chrome builds.
    #
    # Yandex Browser is *not* listed here — yt-dlp errors with
    # ``unsupported browser: "yandex"``. Users on Yandex Browser should
    # export cookies.txt and set ``settings.youtube_cookies_file``.
    _COOKIE_BROWSERS = ("firefox", "brave", "opera", "vivaldi", "chrome", "edge")

    # Optional Netscape-format cookies.txt file. Set by ``main.py`` from
    # ``settings.youtube_cookies_file``; if non-empty and the file exists
    # we hand it to yt-dlp directly via ``cookiefile=``, bypassing all
    # browser auto-detection.
    _cookies_file: Optional[str] = None

    @classmethod
    def set_cookies_file(cls, path: Optional[str]) -> None:
        """Configure the Netscape-format cookies.txt yt-dlp should use.

        ``path`` may be ``None`` or empty to clear. ``main.py`` calls this
        at startup with ``settings.youtube_cookies_file`` and again from
        ``_on_settings_saved`` so the live downloader reflects edits made
        in the Settings window without a restart.
        """
        cleaned = (path or "").strip()
        cls._cookies_file = cleaned or None

    @classmethod
    async def _download_ytdlp(
        cls,
        url: str,
        *,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> Path:
        """Download using yt-dlp (YouTube, RuTube, VK, etc.)."""
        output_template = str(config.temp_dir / "%(id)s.%(ext)s")

        loop = asyncio.get_event_loop()

        # yt-dlp fires this from its worker thread; callers (e.g. the
        # Transcriptor window) marshal UI updates via their own queue.
        def _yt_progress_hook(d: dict) -> None:
            if progress_callback is None:
                return
            status = d.get("status")
            if status == "downloading":
                downloaded = d.get("downloaded_bytes") or 0
                total = (
                    d.get("total_bytes")
                    or d.get("total_bytes_estimate")
                    or 0
                )
                percent = int(downloaded * 100 / total) if total else 0
                speed = (d.get("_speed_str") or "").strip()
                frag_idx = d.get("fragment_index")
                frag_count = d.get("fragment_count")
                frag = (
                    f" · фрагмент {frag_idx}/{frag_count}"
                    if frag_idx and frag_count else ""
                )
                tail = f" · {speed}" if speed else ""
                try:
                    progress_callback(percent, f"Скачиваю {percent}%{tail}{frag}")
                except Exception:
                    pass
            elif status == "finished":
                try:
                    progress_callback(100, "Скачано, обрабатываю…")
                except Exception:
                    pass

        def _base_opts() -> dict:
            opts: dict = {
                # Prefer audio-only, fall back to small video. Without an
                # upper height bound, RuTube and VK happily hand us a
                # 1+ GB HLS-fragmented 1080p stream when all we want is
                # the speech for transcription. Audio-only when offered;
                # otherwise the lightest available muxed stream.
                "format": (
                    "bestaudio/worstaudio/"
                    "worstvideo[height<=480]+bestaudio/"
                    "best[height<=480]/worst"
                ),
                "outtmpl": output_template,
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
                "noplaylist": True,
                # Enable Node.js as a JS runtime. As of yt-dlp 2026.3+, only
                # ``deno`` is auto-enabled — without this option, yt-dlp can't
                # solve YouTube's "n-sig" challenge and returns only storyboard
                # images instead of audio/video. Empty path = auto-find ``node``
                # in PATH.
                "js_runtimes": {"node": {"path": None}},
                "progress_hooks": [_yt_progress_hook],
                # HLS sources (RuTube, VK, some YouTube live) ship hundreds
                # of small ``.ts`` fragments. yt-dlp pulls them one at a
                # time by default, so a 200 MiB stream can take 10+ min
                # over a fast link. 8 parallel fetches saturate residential
                # uplinks without tripping rate-limits.
                "concurrent_fragment_downloads": 8,
                "fragment_retries": 10,
            }
            return opts

        def _run(opts: dict) -> Path:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    raise ValueError("Failed to extract info from URL")
                return Path(ydl.prepare_filename(info))

        def _try_with_cookies_file(path: str) -> Path:
            opts = _base_opts()
            opts["cookiefile"] = path
            return _run(opts)

        def _try_browser(browser: Optional[str]) -> Path:
            opts = _base_opts()
            if browser:
                opts["cookiesfrombrowser"] = (browser, None, None, None)
            return _run(opts)

        def _harvest_browser_cookies() -> Optional[Path]:
            """Read cookies straight out of the user's installed Chromium
            browsers (Yandex / Chrome / Edge / Brave / Opera / Vivaldi),
            decrypt them, and write a ``cookies.txt`` for ``url``'s domain.

            Returns the file path on success or ``None`` if no cookies
            for the relevant domain were found across any browser.
            """
            import os as _os

            domains = _domains_for_url(url)
            if not domains:
                return None
            # mkstemp returns (fd, path). We only want the path; close the
            # fd immediately or Windows keeps the file open and our later
            # ``unlink`` would error with WinError 32.
            fd, name = tempfile.mkstemp(suffix=".cookies.txt")
            _os.close(fd)
            tmp = Path(name)
            try:
                report = export_cookies_for_domains(domains, tmp)
            except Exception:
                logger.exception("auto cookie extract failed")
                tmp.unlink(missing_ok=True)
                return None
            if report["total_rows"] == 0:
                tmp.unlink(missing_ok=True)
                return None
            logger.info(
                "Harvested %d cookies for %s from %s",
                report["total_rows"], domains, report["rows_per_browser"],
            )
            return tmp

        def _download() -> Path:
            # 1) Explicit cookies.txt from settings always wins.
            cfg_cookies = cls._cookies_file
            if cfg_cookies and Path(cfg_cookies).exists():
                return _try_with_cookies_file(cfg_cookies)

            # 2) Vanilla attempt — most non-gated content downloads in one shot.
            first_error = ""
            try:
                return _try_browser(None)
            except Exception as exc:
                first_error = _strip_ansi(str(exc))
                if not _looks_like_bot_check(first_error):
                    raise RuntimeError(first_error) from exc

            # 3) Bot check — auto-extract cookies from the local browsers
            #    using our own Chromium decryption (covers Yandex Browser
            #    and Chrome 127+, both of which yt-dlp's own browser
            #    plumbing fails on).
            harvest_error: Optional[str] = None
            harvested = _harvest_browser_cookies()
            if harvested is not None:
                try:
                    return _try_with_cookies_file(str(harvested))
                except Exception as exc:
                    harvest_error = _strip_ansi(str(exc))
                    logger.warning(
                        "auto-harvested cookies didn't help: %s",
                        harvest_error[:200],
                    )
                finally:
                    Path(harvested).unlink(missing_ok=True)

            # 4) Last resort: yt-dlp's own ``cookies-from-browser``. This
            #    will fail on Chrome 127+ (DPAPI) and on Yandex (unsupported)
            #    but might rescue Firefox-only users.
            errors: list[str] = []
            for browser in cls._COOKIE_BROWSERS:
                try:
                    return _try_browser(browser)
                except Exception as exc:
                    errors.append(f"{browser}: {_strip_ansi(str(exc))[:160]}")

            installed = list_installed_chromium_browsers()
            running = running_browsers()  # dict[browser, count]
            blockers = {b: n for b, n in running.items() if b in installed}

            advice: list[str] = []
            yandex_count = blockers.get("yandex", 0)
            if yandex_count:
                advice.append(
                    f"• Завершите Яндекс.Браузер ПОЛНОСТЬЮ. У вас сейчас "
                    f"{yandex_count} процессов «browser.exe» запущено.\n"
                    f"  Это потому что в Яндекс.Браузере по умолчанию включено "
                    f"«Продолжать работу в фоне». Закрытие окна оставляет фоновые "
                    f"процессы, и они держат файл cookies под exclusive-lock.\n"
                    f"  Как закрыть полностью:\n"
                    f"    a) Откройте Яндекс.Браузер → Меню (☰) → «Закрыть Яндекс.Браузер», или\n"
                    f"    b) Диспетчер задач (Ctrl+Shift+Esc) → найдите «Yandex»/«browser.exe» → «Снять задачу», или\n"
                    f"    c) В Яндекс настройки → найдите «фон» → выключите «Продолжать работу в фоне».\n"
                    f"  После этого нажмите Старт ещё раз — cookies подтянутся."
                )
            if blockers.get("chrome") or blockers.get("edge"):
                advice.append(
                    "• Chrome 127+ и новый Edge зашифровывают cookies «app-bound "
                    "encryption» (yt-dlp issue #10927) — расшифровать снаружи Chrome "
                    "технически невозможно без админ-прав и DLL-инжекта. Закрытие "
                    "браузера тут не поможет."
                )
            advice.append(
                "• Самый надёжный путь: установите Firefox, войдите в YouTube "
                "(или RuTube / VK) один раз — Firefox не использует "
                "app-bound encryption, мы прочитаем его cookies автоматически."
            )
            advice.append(
                "• Альтернатива: экспортируйте cookies.txt расширением "
                "«Get cookies.txt LOCALLY» в любом браузере и укажите файл в "
                "Настройках → «YouTube cookies»."
            )

            running_summary = (
                ", ".join(f"{b}={n}" for b, n in blockers.items())
                if blockers else "—"
            )

            harvest_line = (
                f"Авто-извлечение cookies: получили cookies, но yt-dlp с ними тоже не справился — {harvest_error[:160]}"
                if harvest_error else
                "Авто-извлечение cookies: ничего не нашлось ни в одном браузере."
            )

            raise RuntimeError(
                "Источник требует авторизации, но cookies получить не удалось.\n\n"
                f"Установлено браузеров: {', '.join(installed) or '—'}\n"
                f"Сейчас запущено (мешают чтению cookies): {running_summary}\n"
                f"{harvest_line}\n\n"
                "Что делать:\n" + "\n".join(advice) + "\n\n"
                "Подробности yt-dlp:\n" + "\n".join(errors)
            )

        return await loop.run_in_executor(None, _download)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """yt-dlp's error messages contain ANSI colour escapes that render as
    raw ``[0;31m`` junk in our tk text widget — strip them before display."""
    return _ANSI_RE.sub("", text or "")


_BOT_CHECK_MARKERS = (
    "sign in to confirm you're not a bot",
    "Sign in to confirm",
    "cookies-from-browser",
    "use --cookies",
)


def _looks_like_bot_check(error_text: str) -> bool:
    low = (error_text or "").lower()
    return any(m.lower() in low for m in _BOT_CHECK_MARKERS)


def _extract_filename(url: str) -> Optional[str]:
    """Extract filename from URL."""
    parsed = urlparse(url)
    path = unquote(parsed.path)
    if path:
        name = Path(path).name
        if name and "." in name:
            return name
    return None
