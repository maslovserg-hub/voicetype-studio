"""Right-Ctrl global hotkey dictation.

Architecture (preserves the standalone Voice Type behaviour):

* an always-open ``sounddevice.InputStream`` feeds 1600-sample blocks (100 ms
  @ 16 kHz) into :meth:`_audio_cb`;
* while ``self.recording`` is true, blocks are buffered. ``SILENCE_BLK``
  blocks of below-threshold RMS — or hitting ``MAX_BLOCKS`` — flush the
  buffer to a worker thread that calls the injected ``transcribe_fn``;
* the result is pasted into the foreground-window-at-press-time via the
  Win32 clipboard + ``SendInput Ctrl+V`` dance — typing each word as a
  separate paste avoids Punto Switcher and similar tools mangling the text.

Threading
---------
This module owns three threads:

1. the pynput ``keyboard.Listener`` (started by pynput);
2. the sounddevice audio callback (sounddevice's audio thread);
3. one short-lived worker per utterance that runs ``transcribe_fn``.

``transcribe_fn`` is injected by main.py and is expected to route through
the app-wide ``ThreadPoolExecutor(max_workers=1)`` so GigaAM never sees
overlapping calls.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)


SAMPLE_RATE = 16000
BLOCKSIZE = 1600
SILENCE_TH = 0.004
SILENCE_BLK = 15  # 1.5 s of silence → flush
MAX_BLOCKS = 150  # 15 s max per utterance

# Watchdog cadence — every WATCHDOG_INTERVAL we check that the mic stream
# is still ``active`` and re-open it if not. PortAudio silently aborts the
# stream when Windows revokes mic access (Game Bar privacy toggle, default
# device change, USB mic unplug), so without this the app would sit alive
# in the tray with a dead stream until manually restarted.
WATCHDOG_INTERVAL = 2.0
WATCHDOG_BACKOFF_MAX = 10.0


# Russian-only post-processing — drop garbage Latin tokens and the leading
# "ы" GigaAM sometimes hallucinates before a Russian word.
_NOISE_Y = re.compile(r"(?:^|(?<= ))[ыЫ](?=[а-яёА-ЯЁ])")
_EN_SHORT = {
    "ok", "hi", "no", "yes", "wow", "the", "and", "or", "but",
    "in", "on", "at", "to", "for", "lol", "omg", "bye",
}


def clean_dictation_text(text: str) -> str:
    """Filter ASR noise: stray Latin tokens and ghost ``ы``."""
    def _filter_latin(m: "re.Match[str]") -> str:
        w = m.group()
        if w.lower() in _EN_SHORT:
            return w
        vowels = sum(1 for c in w if c in "aeiouAEIOU")
        if vowels >= 2 and len(w) >= 3:
            return w
        return ""

    text = re.sub(r"[a-zA-Z]+", _filter_latin, text).strip()
    text = _NOISE_Y.sub("", text).strip()
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def _trim_trailing_silence(audio, threshold: float = SILENCE_TH * 0.4):
    """Drop blocks of silence at the very end of the buffer."""
    import numpy as np

    if len(audio) == 0:
        return audio
    rms = [
        float(np.sqrt(np.mean(audio[i : i + BLOCKSIZE] ** 2)))
        for i in range(0, len(audio), BLOCKSIZE)
    ]
    last = next((i for i, r in enumerate(reversed(rms)) if r >= threshold), None)
    if last is None:
        return np.array([], dtype=np.float32)
    return audio[: (len(rms) - last) * BLOCKSIZE]


def _normalize(audio):
    import numpy as np

    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak > 0.01:
        return audio / peak * 0.95
    return audio


# --- Win32 clipboard / SendInput paste machinery -------------------------
#
# Direct Win32 API — pynput's keyboard.Controller.type() is too slow for
# real-time dictation and gets mangled by IME-aware text expanders.

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56
TYPE_WORD_DELAY_S = 0.05


def _win32():
    """Lazy bundle of user32/kernel32 + struct types. None on non-Windows."""
    if sys.platform != "win32":
        return None
    import ctypes
    import ctypes.wintypes as wt

    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    u32.GetForegroundWindow.restype = wt.HWND
    u32.SetForegroundWindow.argtypes = [wt.HWND]
    u32.SetForegroundWindow.restype = wt.BOOL
    u32.BringWindowToTop.argtypes = [wt.HWND]
    u32.BringWindowToTop.restype = wt.BOOL
    u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    u32.GetWindowThreadProcessId.restype = wt.DWORD
    u32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
    u32.AttachThreadInput.restype = wt.BOOL
    u32.OpenClipboard.argtypes = [wt.HWND]
    u32.OpenClipboard.restype = wt.BOOL
    u32.CloseClipboard.restype = wt.BOOL
    u32.EmptyClipboard.restype = wt.BOOL
    u32.GetClipboardData.argtypes = [wt.UINT]
    u32.GetClipboardData.restype = wt.HANDLE
    u32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
    u32.SetClipboardData.restype = wt.HANDLE
    k32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    k32.GlobalAlloc.restype = wt.HANDLE
    k32.GlobalLock.argtypes = [wt.HANDLE]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [wt.HANDLE]
    k32.GlobalUnlock.restype = wt.BOOL
    k32.GlobalFree.argtypes = [wt.HANDLE]
    k32.GlobalFree.restype = wt.HANDLE

    class _KI(ctypes.Structure):
        _fields_ = [
            ("wVk", wt.WORD),
            ("wScan", wt.WORD),
            ("dwFlags", wt.DWORD),
            ("time", wt.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class _IU(ctypes.Union):
        _fields_ = [("ki", _KI), ("_pad", ctypes.c_byte * 32)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", wt.DWORD), ("u", _IU)]

    return {
        "ctypes": ctypes,
        "u32": u32,
        "k32": k32,
        "KI": _KI,
        "IU": _IU,
        "INPUT": _INPUT,
    }


def _clipboard_get_text(api) -> str | None:
    u32, k32, ctypes = api["u32"], api["k32"], api["ctypes"]
    if not u32.OpenClipboard(None):
        return None
    try:
        h = u32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        ptr = k32.GlobalLock(h)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


def _clipboard_set_text(api, text: str) -> bool:
    u32, k32, ctypes = api["u32"], api["k32"], api["ctypes"]
    data = (text + "\0").encode("utf-16-le")
    h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h:
        return False
    ptr = k32.GlobalLock(h)
    if not ptr:
        k32.GlobalFree(h)
        return False
    ctypes.memmove(ptr, data, len(data))
    k32.GlobalUnlock(h)
    if not u32.OpenClipboard(None):
        k32.GlobalFree(h)
        return False
    try:
        u32.EmptyClipboard()
        if not u32.SetClipboardData(CF_UNICODETEXT, h):
            k32.GlobalFree(h)
            return False
        return True
    finally:
        u32.CloseClipboard()


def _send_ctrl_v(api) -> None:
    u32, ctypes = api["u32"], api["ctypes"]
    INPUT, IU, KI = api["INPUT"], api["IU"], api["KI"]
    seq = [
        (VK_CONTROL, 0),
        (VK_V, 0),
        (VK_V, KEYEVENTF_KEYUP),
        (VK_CONTROL, KEYEVENTF_KEYUP),
    ]
    inputs = []
    for vk, fl in seq:
        u = IU()
        u.ki = KI(vk, 0, fl, 0, 0)
        inputs.append(INPUT(1, u))
    arr = (INPUT * len(inputs))(*inputs)
    u32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _force_foreground(api, hwnd: int) -> None:
    """SetForegroundWindow only works from the foreground thread — attach."""
    u32, k32 = api["u32"], api["k32"]
    cur = k32.GetCurrentThreadId()
    fg = u32.GetForegroundWindow()
    fg_t = u32.GetWindowThreadProcessId(fg, None)
    if fg_t and fg_t != cur:
        u32.AttachThreadInput(cur, fg_t, True)
        u32.SetForegroundWindow(hwnd)
        u32.BringWindowToTop(hwnd)
        u32.AttachThreadInput(cur, fg_t, False)
    else:
        u32.SetForegroundWindow(hwnd)


def _paste_text(api, text: str, target_hwnd: int | None) -> None:
    if api is None or not text:
        return
    u32 = api["u32"]
    fg = u32.GetForegroundWindow()
    if target_hwnd and fg != target_hwnd:
        _force_foreground(api, target_hwnd)
        time.sleep(0.1)

    saved = _clipboard_get_text(api)
    chunks = [w + " " for w in text.split(" ") if w]
    for i, chunk in enumerate(chunks):
        if not _clipboard_set_text(api, chunk):
            logger.warning("clipboard set failed mid-typing")
            break
        _send_ctrl_v(api)
        if i < len(chunks) - 1:
            time.sleep(TYPE_WORD_DELAY_S)

    def _restore() -> None:
        time.sleep(0.25)
        if saved is not None:
            _clipboard_set_text(api, saved)

    threading.Thread(target=_restore, daemon=True).start()


# --- main public class ---------------------------------------------------


class DictationListener:
    """Right-Ctrl push-to-talk dictation.

    Parameters
    ----------
    transcribe_fn:
        Callable ``(numpy.ndarray, sample_rate) -> str``. Synchronous —
        main.py wraps :meth:`core.Transcriber.transcribe_array` with the
        shared executor here.
    overlay:
        :class:`desktop.overlay.Overlay` — gets ``show()``/``hide()`` calls
        and a continuous level feed via ``set_level``.
    """

    def __init__(
        self,
        transcribe_fn: Callable[..., str],
        overlay,
    ) -> None:
        self.transcribe_fn = transcribe_fn
        self.overlay = overlay

        self.recording = False
        self.cancelled = False
        self._target_hwnd: int | None = None
        self._blocks: list = []  # list[np.ndarray]
        self._silence = 0
        self._tr_lock = threading.Lock()

        self._listener = None  # pynput Listener
        self._mic_stream = None  # sounddevice.InputStream
        self._stream_lock = threading.Lock()
        self._watchdog_thread: threading.Thread | None = None
        self._watchdog_stop = threading.Event()
        self._win32 = _win32()  # cached or None

    # --- lifecycle -----------------------------------------------------

    def start(self) -> None:
        """Open the mic stream and start the global hotkey listener."""
        from pynput import keyboard

        if self._listener is not None:
            return  # already started

        self._open_mic_stream()  # best-effort; watchdog will retry on failure

        self._listener = keyboard.Listener(on_press=self._on_press)
        self._listener.start()

        self._watchdog_stop.clear()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            daemon=True,
            name="dictation-watchdog",
        )
        self._watchdog_thread.start()
        logger.info("Dictation listener started")

    def stop(self) -> None:
        """Cancel any in-flight utterance and shut down mic + hotkey."""
        self.cancelled = True
        self.recording = False

        self._watchdog_stop.set()
        if self._watchdog_thread is not None:
            try:
                self._watchdog_thread.join(timeout=1.0)
            except Exception:
                pass
            self._watchdog_thread = None

        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

        with self._stream_lock:
            if self._mic_stream is not None:
                try:
                    self._mic_stream.stop()
                    self._mic_stream.close()
                except Exception:
                    pass
                self._mic_stream = None

        try:
            self.overlay.hide()
        except Exception:
            pass
        logger.info("Dictation listener stopped")

    # --- mic stream management ----------------------------------------

    def _open_mic_stream(self) -> bool:
        """Open (or reopen) the InputStream. Returns True on success.

        Safe to call repeatedly — closes any existing stream first. The
        watchdog uses this to recover from PortAudio aborts (Game Bar mic
        revoke, default-device swap, USB unplug, sleep/resume).
        """
        import sounddevice as sd

        with self._stream_lock:
            # Tear down any dead/old stream first.
            if self._mic_stream is not None:
                try:
                    self._mic_stream.stop()
                    self._mic_stream.close()
                except Exception:
                    pass
                self._mic_stream = None

            try:
                stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    blocksize=BLOCKSIZE,
                    dtype="float32",
                    callback=self._audio_cb,
                )
                stream.start()
                self._mic_stream = stream
                logger.info("Mic stream opened")
                return True
            except Exception as exc:
                # Common when mic is blocked (Game Bar privacy) or no device
                # is plugged in. Watchdog will keep retrying with backoff.
                logger.warning("Failed to open mic stream: %s", exc)
                return False

    def _stream_alive(self) -> bool:
        stream = self._mic_stream
        if stream is None:
            return False
        try:
            return bool(stream.active)
        except Exception:
            return False

    def _watchdog_loop(self) -> None:
        """Re-open the mic stream whenever it dies.

        Runs every ~``WATCHDOG_INTERVAL`` seconds. On consecutive failures
        backs off up to ``WATCHDOG_BACKOFF_MAX`` so a permanently absent
        device doesn't spam the logs. Skips re-open while the user is
        mid-utterance to avoid yanking ``_blocks`` from under ``_audio_cb``.
        """
        backoff = WATCHDOG_INTERVAL
        while not self._watchdog_stop.wait(backoff):
            if self._stream_alive():
                backoff = WATCHDOG_INTERVAL
                continue
            if self.recording:
                # Don't disturb an in-flight utterance — _stop_flush will
                # finish writing _blocks; we retry on the next tick.
                continue
            if self._open_mic_stream():
                backoff = WATCHDOG_INTERVAL
            else:
                backoff = min(backoff * 2, WATCHDOG_BACKOFF_MAX)

    # --- audio + hotkey callbacks --------------------------------------

    def _audio_cb(self, indata, frames, time_info, status) -> None:
        import numpy as np

        if status:
            # PortAudio status flags (input_overflow etc). Most are
            # recoverable, but if the stream is being aborted this is
            # often the only signal we get — log so the watchdog's
            # subsequent reopen has a paper trail.
            logger.debug("audio status: %s", status)

        blk = indata[:, 0].copy()
        rms = float(np.sqrt(np.mean(blk ** 2)))
        # Update overlay level even when not recording is harmless if
        # overlay is hidden — it just won't be drawn. But keep pre-record
        # level at 0 to avoid the bars twitching while idle.
        self.overlay.set_level(rms if self.recording else 0.0)
        if not self.recording:
            return
        self._blocks.append(blk)
        self._silence = self._silence + 1 if rms < SILENCE_TH else 0
        if self._silence >= SILENCE_BLK or len(self._blocks) >= MAX_BLOCKS:
            self._flush()

    def _on_press(self, key) -> None:
        from pynput import keyboard

        if key == keyboard.Key.ctrl_r:
            if not self.recording:
                self._begin_recording()
            else:
                self._end_recording(cancel=False)
        elif key == keyboard.Key.esc:
            if self.recording:
                self._end_recording(cancel=True)

    # --- recording flow ------------------------------------------------

    def _begin_recording(self) -> None:
        self._blocks = []
        self._silence = 0
        self.cancelled = False
        self.recording = True
        if self._win32 is not None:
            self._target_hwnd = self._win32["u32"].GetForegroundWindow()
        else:
            self._target_hwnd = None
        self.overlay.show()

    def _end_recording(self, *, cancel: bool) -> None:
        self.cancelled = cancel
        self.recording = False
        threading.Thread(target=self._stop_flush, daemon=True).start()

    def _stop_flush(self) -> None:
        # Give the audio callback a beat to drain in-flight blocks.
        time.sleep(0.5)
        if not self.cancelled:
            self._flush()
        else:
            self._blocks, self._silence = [], 0
        self.overlay.hide()

    def _flush(self) -> None:
        import numpy as np

        if not self._blocks:
            return
        audio = np.concatenate(self._blocks)
        self._blocks, self._silence = [], 0
        threading.Thread(
            target=self._transcribe_and_paste,
            args=(audio,),
            daemon=True,
        ).start()

    def _transcribe_and_paste(self, audio) -> None:
        try:
            audio = _trim_trailing_silence(audio)
            if len(audio) < SAMPLE_RATE * 0.3:
                return  # under 0.3 s — almost certainly noise
            audio = _normalize(audio)
            with self._tr_lock:
                raw = self.transcribe_fn(audio, SAMPLE_RATE)
                text = clean_dictation_text(raw or "")
                logger.info(
                    "dictation raw=%r clean=%r cancelled=%s hwnd=%s",
                    raw, text, self.cancelled, self._target_hwnd,
                )
                if text and not self.cancelled:
                    _paste_text(self._win32, text, self._target_hwnd)
        except Exception:
            logger.exception("Dictation transcribe/paste failed")
