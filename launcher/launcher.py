"""VoiceType Studio Launcher — small (~5 MB) bootstrapper that downloads
the main app on first run and launches it on every subsequent run.

The full Studio bundle (~280 MB) is too big for direct distribution; we
ship the launcher as the user-facing exe. Launcher → GitHub Releases zip
→ unpack to ``%APPDATA%\\VoiceTypeStudio\\`` → run ``VoiceTypeStudio.exe``.

If the install dir already contains the exe, the launcher detects it and
just re-launches without touching the network.

Three extra phases run after install (skipped on subsequent runs when the
app is already in place):

1. FFmpeg check — pydub and yt-dlp shell out to ``ffmpeg`` for every
   non-WAV input. If it's not on PATH we offer:

   - **Установить (winget)** — silent ``winget install Gyan.FFmpeg``
     scoped to the user; PATH is spliced in-process so the launched app
     sees it without a reboot.
   - **Скачать вручную** — opens gyan.dev in a browser.
   - **Пропустить** — launch anyway; convert will fail at runtime with a
     clearer error than a mysterious silent crash.

2. Model check — looks for GigaAM ``v3_e2e_ctc.ckpt`` in ``C:/gigaam_cache``.
   Missing? Show a dialog with three options:

   - **Указать папку** — user points to an existing folder containing the
     model files; launcher writes that path into ``gigaam_cache_path.txt``
     next to the installed exe. ``core.config._resolve_gigaam_cache`` reads
     this on every launch, so the model stays in the user's folder — no
     440 МБ copy, no env-var dance.
   - **Скачать сейчас** — direct download from the Sber CDN (~423 МБ).
   - **Пропустить** — let the app download lazily on first ASR call.

   This mirrors the first-run wizard from the old ``my-voice-assistent``
   but keeps the choice in the launcher so the main app starts cold and
   fast.

3. Start-menu shortcut — best-effort ``.lnk`` drop into the per-user
   Start menu via PowerShell + ``WScript.Shell`` (no pywin32 dep).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.filedialog as fd
import urllib.request as ur
import webbrowser
import zipfile

# TODO(release): point this at the real GitHub Releases asset before publish.
APP_URL = (
    "https://github.com/maslovserg-hub/voicetype-studio/releases/latest/download/"
    "VoiceTypeStudio_release.zip"
)
INSTALL_DIR = os.path.join(
    os.environ.get("APPDATA", "C:/VoiceTypeStudio"), "VoiceTypeStudio"
)
# Flat layout — the zip is built from ``dist/VoiceTypeStudio/*`` and
# extracted into ``INSTALL_DIR``, so the exe lands one level down, not
# two. Matching this keeps the path predictable for ``already_installed()``.
EXE_PATH = os.path.join(INSTALL_DIR, "VoiceTypeStudio.exe")
# Pointer file the app reads from next to its exe — must match
# ``core.config._GIGAAM_POINTER_FILE``.
POINTER_FILE = os.path.join(INSTALL_DIR, "gigaam_cache_path.txt")

# GigaAM model — same defaults as ``core.config.AppConfig``. We can't
# import core/ from the launcher (the app isn't installed yet at this
# point), so we duplicate the constants. If they ever change in core/,
# update them here too.
GIGAAM_CACHE = "C:/gigaam_cache"
MODEL_NAME = "v3_e2e_ctc"
MODEL_CDN = "https://cdn.chatwm.opensmodel.sberdevices.ru/GigaAM"
# Each entry: (filename, full-download size in bytes, minimum acceptable
# size for "looks valid" check). The CDN tokenizer is ~240 KB — the
# 1 MB threshold I used at first rejected genuine installs, so we
# split the validation thresholds per file.
MODEL_FILES = [
    (f"{MODEL_NAME}.ckpt",            422 * 1024 * 1024, 100 * 1024 * 1024),
    (f"{MODEL_NAME}_tokenizer.model",   1 * 1024 * 1024,             1024),
]

BG, ACC, WHITE, GRAY, PILL = (
    "#1a1a2e", "#e05a00", "#f8f8f2", "#888888", "#252540",
)


# ---- pure helpers -------------------------------------------------------


def _start_menu_shortcut_path() -> str:
    """Path of the .lnk we drop into the per-user Start menu."""
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(
        appdata,
        "Microsoft", "Windows", "Start Menu", "Programs",
        "VoiceType Studio.lnk",
    )


def already_installed() -> bool:
    return os.path.isfile(EXE_PATH)


def shortcut_exists() -> bool:
    return os.path.isfile(_start_menu_shortcut_path())


def create_start_menu_shortcut() -> bool:
    """Create a per-user Start-menu shortcut pointing at the installed exe.

    Uses ``IShellLinkW`` directly via ``win32com.shell`` — the Unicode
    variant of the shell-link COM interface. The ANSI wrappers
    (``WScript.Shell.CreateShortcut`` / PowerShell COM dispatch) fail on
    Cyrillic paths like ``C:\\Users\\Сергей\\AppData\\...`` with a
    nondescript ``ArgumentException``.

    Returns ``True`` on success (or when the shortcut already exists);
    ``False`` when pywin32 isn't available or the COM call fails.
    """
    if sys.platform != "win32":
        return False
    if shortcut_exists():
        return True
    if not os.path.isfile(EXE_PATH):
        return False

    shortcut_path = _start_menu_shortcut_path()
    os.makedirs(os.path.dirname(shortcut_path), exist_ok=True)
    working_dir = os.path.dirname(EXE_PATH)

    try:
        import pythoncom
        from win32com.shell import shell as win_shell
    except ImportError:
        return False

    try:
        link = pythoncom.CoCreateInstance(
            win_shell.CLSID_ShellLink, None,
            pythoncom.CLSCTX_INPROC_SERVER, win_shell.IID_IShellLinkW,
        )
        link.SetPath(EXE_PATH)
        link.SetWorkingDirectory(working_dir)
        link.SetIconLocation(EXE_PATH, 0)
        link.SetDescription("VoiceType Studio")
        persist = link.QueryInterface(pythoncom.IID_IPersistFile)
        persist.Save(shortcut_path, True)
    except Exception:
        return False
    return shortcut_exists()


def model_present(folder: str = GIGAAM_CACHE) -> bool:
    """True if both GigaAM files exist in ``folder`` at plausible sizes.

    Per-file minimums catch half-downloaded checkpoints that
    ``gigaam.load_model`` would happily try to load and then crash on.
    The tokenizer is genuinely small (~240 КБ) so its threshold is
    relaxed.
    """
    for fname, _, fmin in MODEL_FILES:
        fp = os.path.join(folder, fname)
        if not (os.path.isfile(fp) and os.path.getsize(fp) >= fmin):
            return False
    return True


def ffmpeg_available() -> bool:
    """True if ``ffmpeg`` is reachable on PATH.

    The Studio's audio pipeline (pydub + yt-dlp post-processors) shells
    out to ``ffmpeg``; without it transcription falls over for every
    file that isn't already 16 kHz mono WAV.
    """
    return shutil.which("ffmpeg") is not None


def install_ffmpeg_via_winget(timeout_s: int = 600) -> bool:
    """Run ``winget install Gyan.FFmpeg`` silently. Returns ``True`` if
    winget exited cleanly AND ffmpeg is now on PATH.

    User-scope install — no admin prompt on Windows 10 1809+ with App
    Installer. Returns ``False`` on any failure (no winget, network
    issue, user declined UAC, etc.). PATH is refreshed by re-checking
    via :func:`shutil.which`, which reads the live environment.
    """
    if sys.platform != "win32":
        return False
    if shutil.which("winget") is None:
        return False
    try:
        # ``--silent`` skips installer prompts; the source/agreement
        # flags suppress the "do you accept the MS Store TOS?" dialog
        # that otherwise blocks a non-interactive run.
        result = subprocess.run(
            [
                "winget", "install", "--id", "Gyan.FFmpeg",
                "--silent",
                "--accept-source-agreements",
                "--accept-package-agreements",
                "--scope", "user",
            ],
            capture_output=True, timeout=timeout_s,
            creationflags=0x08000000,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    # winget updates PATH in the installer's environment, but our
    # already-running process inherited the old PATH. Look in the
    # well-known install dir as a fallback.
    if ffmpeg_available():
        return True
    winget_dir = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Microsoft", "WinGet", "Packages",
    )
    if os.path.isdir(winget_dir):
        for root, _dirs, files in os.walk(winget_dir):
            if "ffmpeg.exe" in files:
                # Splice the bin dir into our process PATH so the model
                # check and the launched app see it without a reboot.
                os.environ["PATH"] = root + os.pathsep + os.environ.get("PATH", "")
                return True
    return False


def _pointer_folder() -> str | None:
    """Read the pointer file the launcher writes for "use my existing
    folder" flow. Returns the stored path if it exists and is a folder,
    else ``None``."""
    if not os.path.isfile(POINTER_FILE):
        return None
    try:
        with open(POINTER_FILE, "r", encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError:
        return None
    return raw if raw and os.path.isdir(raw) else None


def model_resolved() -> bool:
    """True if the model is either in the default cache or reachable via
    the pointer file."""
    if model_present(GIGAAM_CACHE):
        return True
    pf = _pointer_folder()
    return pf is not None and model_present(pf)


def launch() -> None:
    # creationflags=DETACHED_PROCESS so the launcher window can close
    # without taking the main app down with it.
    #
    # ``os._exit`` instead of ``sys.exit`` because Tkinter swallows
    # ``SystemExit`` raised from ``after`` callbacks — the launcher
    # window would otherwise stay open after Popen succeeds.
    subprocess.Popen([EXE_PATH], creationflags=0x00000008)
    os._exit(0)


# ---- app install (download + extract zip) -------------------------------


def _download_app_zip(set_progress, status_var, win: tk.Tk) -> None:
    os.makedirs(INSTALL_DIR, exist_ok=True)
    zip_path = os.path.join(INSTALL_DIR, "VoiceTypeStudio.zip")

    for attempt in range(3):
        try:
            req = ur.Request(
                APP_URL,
                headers={"User-Agent": "VoiceTypeStudio-Launcher/1.0"},
            )
            with ur.urlopen(req, timeout=300) as src:
                total = int(src.headers.get("Content-Length", 0))
                downloaded = 0
                with open(zip_path, "wb") as f:
                    while True:
                        buf = src.read(65536)
                        if not buf:
                            break
                        f.write(buf)
                        downloaded += len(buf)
                        frac = (downloaded / total * 0.85) if total else 0.1
                        mb = downloaded / 1024 / 1024
                        win.after(
                            0, set_progress, frac,
                            f"Скачивание приложения: {mb:.0f} МБ",
                        )
            break
        except Exception:
            if attempt < 2:
                win.after(0, status_var.set, f"Повтор {attempt + 2}/3…")
                time.sleep(3)
            else:
                raise

    win.after(0, set_progress, 0.88, "Распаковка…")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(INSTALL_DIR)
    os.unlink(zip_path)
    win.after(0, set_progress, 1.0, "Приложение установлено.")


# ---- model fetch (browse or download) -----------------------------------


def _point_at_model_folder(src_folder: str) -> bool:
    """Write ``src_folder`` into the pointer file next to the installed
    exe, so :func:`core.config._resolve_gigaam_cache` picks it up on the
    next launch.

    Returns ``True`` on success, ``False`` if the folder doesn't contain
    the model files we expect.
    """
    if not model_present(src_folder):
        return False
    os.makedirs(os.path.dirname(POINTER_FILE), exist_ok=True)
    with open(POINTER_FILE, "w", encoding="utf-8") as f:
        f.write(src_folder)
    return True


def _download_model(set_progress, status_var, win: tk.Tk) -> bool:
    """Download GigaAM files from the Sber CDN. Returns success."""
    os.makedirs(GIGAAM_CACHE, exist_ok=True)
    total_bytes = sum(fsize for _, fsize, _ in MODEL_FILES)
    downloaded_so_far = 0

    for fname, fsize, _ in MODEL_FILES:
        dest = os.path.join(GIGAAM_CACHE, fname)
        if os.path.isfile(dest) and os.path.getsize(dest) >= fsize * 0.99:
            downloaded_so_far += fsize
            continue

        url = f"{MODEL_CDN}/{fname}"
        tmp = dest + ".part"
        for attempt in range(3):
            try:
                req = ur.Request(url, headers={"User-Agent": "VoiceTypeStudio/1.0"})
                with ur.urlopen(req, timeout=60) as src, open(tmp, "wb") as out:
                    got = 0
                    while True:
                        buf = src.read(65536)
                        if not buf:
                            break
                        out.write(buf)
                        got += len(buf)
                        mb = (downloaded_so_far + got) / 1024 / 1024
                        frac = min(
                            (downloaded_so_far + got) / total_bytes, 0.99,
                        )
                        win.after(
                            0, set_progress, frac,
                            f"Скачивание модели: {mb:.0f} / ~423 МБ",
                        )
                os.replace(tmp, dest)
                downloaded_so_far += fsize
                break
            except Exception:
                if os.path.isfile(tmp):
                    try:
                        os.unlink(tmp)
                    except Exception:
                        pass
                if attempt < 2:
                    win.after(
                        0, status_var.set,
                        f"Ошибка, повтор {attempt + 2}/3…",
                    )
                    time.sleep(3)
                else:
                    return False

    win.after(0, set_progress, 1.0, "Модель скачана.")
    return True


# ---- UI flow ------------------------------------------------------------


def _show_ffmpeg_dialog(win: tk.Tk, set_progress, status_var, on_done) -> None:
    """Ask the user how to provide ffmpeg. Three buttons: auto-install via
    winget, open the gyan.dev download page in a browser, or skip.

    Skip is non-fatal — the app launches normally, but transcribing
    anything but a 16 kHz mono WAV will error at convert time."""
    for w in win.winfo_children():
        if getattr(w, "_persistent", False):
            continue
        w.destroy()

    status_var.set(
        "Для конвертации видео и аудио нужен FFmpeg. Не нашли его в PATH."
    )
    set_progress(0.0, status_var.get())

    btn_frame = tk.Frame(win, bg=BG)
    btn_frame.pack(pady=10)
    btn_style = dict(
        font=("Segoe UI", 10), relief="flat",
        padx=12, pady=6, cursor="hand2",
    )

    def _disable_all() -> None:
        for w in btn_frame.winfo_children():
            try:
                w.configure(state="disabled")
            except tk.TclError:
                pass

    def _on_winget() -> None:
        _disable_all()

        def _do() -> None:
            win.after(0, status_var.set, "Устанавливаю FFmpeg через winget…")
            win.after(0, set_progress, 0.3, "Устанавливаю FFmpeg через winget…")
            ok = install_ffmpeg_via_winget()
            if ok:
                win.after(0, set_progress, 1.0, "FFmpeg установлен.")
                win.after(500, on_done)
            else:
                win.after(0, status_var.set,
                          "Не удалось установить через winget. Попробуйте «Скачать вручную».")
                win.after(0, lambda: [
                    w.configure(state="normal") for w in btn_frame.winfo_children()
                ])

        threading.Thread(target=_do, daemon=True).start()

    def _on_browser() -> None:
        webbrowser.open("https://www.gyan.dev/ffmpeg/builds/")
        status_var.set(
            "Скачайте ffmpeg-release-essentials.zip, "
            "распакуйте и добавьте папку bin в PATH. Потом нажмите «Пропустить»."
        )

    def _on_skip() -> None:
        on_done()

    tk.Button(
        btn_frame, text="Установить (winget)", bg=ACC, fg=WHITE,
        activebackground=PILL, activeforeground=WHITE,
        command=_on_winget, **btn_style,
    ).pack(side="left", padx=4)
    tk.Button(
        btn_frame, text="Скачать вручную", bg=PILL, fg=WHITE,
        activebackground=ACC, activeforeground=WHITE,
        command=_on_browser, **btn_style,
    ).pack(side="left", padx=4)
    tk.Button(
        btn_frame, text="Пропустить", bg=BG, fg=GRAY,
        activebackground=PILL, activeforeground=WHITE,
        command=_on_skip, **btn_style,
    ).pack(side="left", padx=4)


def _show_model_dialog(win: tk.Tk, set_progress, status_var, on_done) -> None:
    """Swap the install screen into a 3-button "what to do about the
    model?" picker. Calls ``on_done()`` when the user has either resolved
    the model or chosen to skip."""

    # Clear existing widgets that aren't header/progress.
    for w in win.winfo_children():
        if getattr(w, "_persistent", False):
            continue
        w.destroy()

    status_var.set(
        "Модель GigaAM (~423 МБ) не найдена. Что сделать?"
    )
    set_progress(0.0, status_var.get())

    btn_frame = tk.Frame(win, bg=BG)
    btn_frame.pack(pady=10)
    btn_style = dict(
        font=("Segoe UI", 10), relief="flat",
        padx=14, pady=6, cursor="hand2",
    )

    def _disable_all() -> None:
        for w in btn_frame.winfo_children():
            try:
                w.configure(state="disabled")
            except tk.TclError:
                pass

    def _on_browse() -> None:
        folder = fd.askdirectory(title="Выберите папку с моделью GigaAM")
        if not folder:
            return
        ok = _point_at_model_folder(folder)
        if ok:
            set_progress(1.0, "Модель найдена, путь сохранён.")
            win.after(500, on_done)
        else:
            status_var.set("В выбранной папке нет файлов v3_e2e_ctc.*")

    def _on_download() -> None:
        _disable_all()

        def _do() -> None:
            ok = _download_model(set_progress, status_var, win)
            if ok:
                win.after(500, on_done)
            else:
                win.after(0, status_var.set, "Не удалось скачать. Попробуйте «Указать папку».")
                win.after(0, lambda: [
                    w.configure(state="normal") for w in btn_frame.winfo_children()
                ])

        threading.Thread(target=_do, daemon=True).start()

    def _on_skip() -> None:
        # App will download on first ASR call. Move on.
        on_done()

    tk.Button(
        btn_frame, text="Указать папку", bg=PILL, fg=WHITE,
        activebackground=ACC, activeforeground=WHITE,
        command=_on_browse, **btn_style,
    ).pack(side="left", padx=4)
    tk.Button(
        btn_frame, text="Скачать сейчас", bg=ACC, fg=WHITE,
        activebackground=PILL, activeforeground=WHITE,
        command=_on_download, **btn_style,
    ).pack(side="left", padx=4)
    tk.Button(
        btn_frame, text="Пропустить", bg=BG, fg=GRAY,
        activebackground=PILL, activeforeground=WHITE,
        command=_on_skip, **btn_style,
    ).pack(side="left", padx=4)


def run_installer() -> None:
    # Fast path: app + model already in place — just launch. Still
    # make sure the Start-menu shortcut is there: ``create_...`` is
    # a no-op when ``shortcut_exists()`` returns True, so calling
    # it on every launch is cheap and catches users whose first run
    # raced past shortcut creation (e.g., an earlier broken build).
    if already_installed() and model_resolved():
        create_start_menu_shortcut()
        launch()

    win = tk.Tk()
    win.title("VoiceType Studio — установка")
    win.configure(bg=BG)
    win.resizable(False, False)
    win.geometry("460x260")
    win.eval("tk::PlaceWindow . center")
    win.protocol("WM_DELETE_WINDOW", sys.exit)
    # Same .ico as the main app so the installer window matches the
    # final app's taskbar look. Best-effort: missing assets is silent.
    if getattr(sys, "frozen", False):
        ico = os.path.join(getattr(sys, "_MEIPASS", ""), "assets", "icon.ico")
    else:
        ico = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "assets", "icon.ico",
        )
    if os.path.isfile(ico):
        try:
            win.iconbitmap(default=ico)
        except Exception:
            pass

    header = tk.Label(
        win, text="VoiceType Studio", font=("Segoe UI", 18, "bold"),
        fg=ACC, bg=BG,
    )
    header._persistent = True  # type: ignore[attr-defined]
    header.pack(pady=(24, 4))

    status_var = tk.StringVar(value="Подготовка…")
    status = tk.Label(
        win, textvariable=status_var, font=("Segoe UI", 10),
        fg=WHITE, bg=BG, wraplength=420, justify="center",
    )
    status._persistent = True  # type: ignore[attr-defined]
    status.pack(pady=(0, 12))

    canvas = tk.Canvas(
        win, width=400, height=8, bg=PILL, highlightthickness=0,
    )
    canvas._persistent = True  # type: ignore[attr-defined]
    canvas.pack()
    bar = canvas.create_rectangle(0, 0, 0, 8, fill=ACC, outline="")

    def set_progress(frac: float, text: str) -> None:
        canvas.coords(bar, 0, 0, int(400 * frac), 8)
        canvas.update_idletasks()
        status_var.set(text)

    def _on_model_done() -> None:
        set_progress(1.0, "Готово! Запускаю…")
        win.after(600, launch)

    def _model_phase() -> None:
        if model_resolved():
            _on_model_done()
        else:
            _show_model_dialog(win, set_progress, status_var, _on_model_done)

    def _ffmpeg_phase() -> None:
        if ffmpeg_available():
            _model_phase()
        else:
            _show_ffmpeg_dialog(win, set_progress, status_var, _model_phase)

    def _install_app() -> None:
        try:
            if not already_installed():
                _download_app_zip(set_progress, status_var, win)
            # Try once to drop a Start-menu shortcut. Best-effort: a failure
            # (no PowerShell, locked profile, etc.) shouldn't block launch.
            if not shortcut_exists():
                create_start_menu_shortcut()
            win.after(0, _ffmpeg_phase)
        except Exception as exc:
            win.after(0, status_var.set, f"Ошибка: {exc}")

    threading.Thread(target=_install_app, daemon=True).start()
    win.mainloop()


if __name__ == "__main__":
    run_installer()
