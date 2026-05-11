import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: List[Word] = field(default_factory=list)


class Transcriber:
    """GigaAM transcription wrapper.

    Two operating modes:
    * longform — single call to ``model.transcribe_longform`` on the whole WAV.
      Requires ``pip install "gigaam[longform]"`` and ``HF_TOKEN`` env var with
      access to ``pyannote/segmentation-3.0``. Produces accurate per-segment
      timestamps for free.
    * chunked  — fallback. We split the WAV into short pieces (≤22s) by silence
      and call ``model.transcribe`` on each piece, reconstructing absolute
      timestamps from the slice offset. Coarser timing but no extra deps.

    Mode is auto-detected on first model load.
    """

    _model = None
    # Model name comes from ``config.gigaam_model`` — see ``_resolve_model_name``.
    # Resolved lazily so test code that overrides ``config.gigaam_model`` via
    # ``monkeypatch`` is honoured.
    _model_name: Optional[str] = None
    _use_longform: Optional[bool] = None

    @classmethod
    def _resolve_model_name(cls) -> str:
        """Single source of truth for the GigaAM model id.

        Pinned to ``config.gigaam_model`` (= ``v3_e2e_ctc``) — the punctuated
        end-to-end CTC checkpoint. We deliberately do NOT honour a
        ``GIGAAM_MODEL`` env var: that's how the older transcription-bot
        accidentally fell through to the unpunctuated ``v3_ctc`` whenever
        ``.env`` wasn't loaded, downloading 421 MB of the wrong model.
        Studio settings UI also doesn't expose model choice — see
        ``feedback_no_giga_model_in_settings.md``.
        """
        from .config import config

        if cls._model_name is None:
            cls._model_name = config.gigaam_model
        return cls._model_name
    _executor = None  # set via set_executor() — see comment on the method

    @classmethod
    def set_executor(cls, executor) -> None:
        """Route all blocking GigaAM calls through ``executor``.

        ``main.py`` calls this once at startup with the app's shared
        ``ThreadPoolExecutor(max_workers=1)`` so dictation, the transcriptor
        window, and the bot all serialize against the same single worker —
        the model itself isn't reentrant-safe.

        ``None`` (the default) restores the loop's default executor.
        """
        cls._executor = executor

    @classmethod
    def _detect_longform_support(cls) -> bool:
        if not os.getenv("HF_TOKEN", "").strip():
            return False
        try:
            import pyannote.audio  # noqa: F401
        except ImportError:
            return False
        return True

    @classmethod
    def _load_model(cls) -> None:
        if cls._model is not None:
            return

        import gigaam
        import torch

        from .config import config

        # Source of truth: config.gigaam_cache_dir (which itself honours the
        # GIGAAM_CACHE_DIR env var with a fallback to C:/gigaam_cache). Reading
        # the env directly here would bypass the config singleton and skip the
        # 422 MB model the user has already cached at the project default.
        cache_dir = str(config.gigaam_cache_dir)
        model_name = cls._resolve_model_name()
        logger.info(
            "Loading GigaAM model: %s (cache_dir=%s)",
            model_name, cache_dir,
        )
        cls._model = gigaam.load_model(
            model_name, device="cpu", download_root=cache_dir,
        )

        # CPU dynamic int8 quantization on Linear layers — same trick the
        # standalone Voice Type app uses. Cuts inference time roughly in half
        # on a 4-core laptop CPU at no measurable accuracy cost for ASR.
        try:
            torch.quantization.quantize_dynamic(
                cls._model, {torch.nn.Linear}, dtype=torch.qint8, inplace=True
            )
        except Exception:  # pragma: no cover — never fatal, just slower
            logger.warning("Dynamic quantization failed; running unquantized.")

        if cls._use_longform is None:
            cls._use_longform = cls._detect_longform_support()
            logger.info(
                "GigaAM transcription mode: %s",
                "longform (pyannote)" if cls._use_longform else "chunked (silence split)",
            )

    @classmethod
    def transcribe_array(cls, audio, sample_rate: int = 16000) -> str:
        """Synchronous one-shot transcription of an in-memory float32 audio array.

        Used by the dictation hotkey path — caller is expected to drive this
        through the app's shared ``ThreadPoolExecutor(max_workers=1)`` so the
        GigaAM model never sees concurrent calls (it isn't reentrant-safe).

        Returns plain text without word timestamps; dictation doesn't need
        them.
        """
        import os as _os
        import tempfile
        import wave

        import numpy as np

        cls._load_model()

        arr = np.asarray(audio, dtype=np.float32)
        peak = float(np.max(np.abs(arr))) if arr.size else 0.0
        if peak > 1.0:
            arr = arr / peak * 0.95
        int16 = (arr * 32767.0).clip(-32768, 32767).astype(np.int16)

        fd, tmp = tempfile.mkstemp(suffix=".wav")
        _os.close(fd)
        try:
            with wave.open(tmp, "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(sample_rate)
                f.writeframes(int16.tobytes())
            result = cls._model.transcribe(tmp)
            return (getattr(result, "text", "") or str(result) or "").strip()
        finally:
            try:
                _os.unlink(tmp)
            except OSError:
                pass

    @classmethod
    async def transcribe(
        cls,
        wav_path: Path,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> List[Segment]:
        """Transcribe a WAV file of arbitrary length.

        Returns segments with absolute timestamps in seconds.
        """
        loop = asyncio.get_event_loop()

        def _ensure_loaded():
            cls._load_model()

        await loop.run_in_executor(cls._executor, _ensure_loaded)

        if cls._use_longform:
            return await cls._transcribe_longform(wav_path, progress_callback)
        return await cls._transcribe_chunked(wav_path, progress_callback)

    @classmethod
    async def _transcribe_longform(
        cls,
        wav_path: Path,
        progress_callback: Optional[Callable[[int], None]],
    ) -> List[Segment]:
        loop = asyncio.get_event_loop()

        def _do() -> List[Segment]:
            result = cls._model.transcribe_longform(str(wav_path))
            out: List[Segment] = []
            for seg in result.segments:
                text = (seg.text or "").strip()
                if not text:
                    continue
                out.append(Segment(start=float(seg.start), end=float(seg.end), text=text))
            return out

        if progress_callback:
            progress_callback(5)
        segments = await loop.run_in_executor(cls._executor, _do)
        if progress_callback:
            progress_callback(100)
        return segments

    @classmethod
    async def _transcribe_chunked(
        cls,
        wav_path: Path,
        progress_callback: Optional[Callable[[int], None]],
    ) -> List[Segment]:
        from .chunking import split_for_short_asr

        loop = asyncio.get_event_loop()

        def _split():
            return split_for_short_asr(wav_path)

        chunks = await loop.run_in_executor(cls._executor, _split)
        total = len(chunks)
        if total == 0:
            return []

        all_segments: List[Segment] = []
        failed_chunks = 0
        for i, (chunk_path, offset_s) in enumerate(chunks):
            def _do(p=chunk_path):
                return cls._model.transcribe(str(p), word_timestamps=True)

            try:
                result = await loop.run_in_executor(cls._executor, _do)
            except Exception:
                # Don't lose the whole transcript over one bad chunk —
                # GigaAM's STFT can blow up on edge-case durations
                # (n_fft=320 wants > 20 ms input) or on rare encoder
                # NaNs. Skip the chunk, log, continue.
                failed_chunks += 1
                logger.warning(
                    "GigaAM failed on chunk %d/%d (offset=%.1fs); skipping",
                    i + 1, total, offset_s,
                    exc_info=True,
                )
                if progress_callback:
                    progress_callback(int((i + 1) / total * 100))
                continue
            text = (getattr(result, "text", "") or str(result) or "").strip()

            if text:
                gigaam_words = getattr(result, "words", None) or []
                if gigaam_words:
                    rel_start = float(gigaam_words[0].start)
                    rel_end = float(gigaam_words[-1].end)
                    abs_words = [
                        Word(
                            start=offset_s + float(w.start),
                            end=offset_s + float(w.end),
                            text=w.text,
                        )
                        for w in gigaam_words
                    ]
                else:
                    rel_start = 0.0
                    rel_end = _wav_duration_seconds(chunk_path)
                    abs_words = []
                all_segments.append(
                    Segment(
                        start=offset_s + rel_start,
                        end=offset_s + rel_end,
                        text=text,
                        words=abs_words,
                    )
                )

            if progress_callback:
                progress_callback(int((i + 1) / total * 100))

        if failed_chunks:
            logger.info(
                "Chunked ASR finished with %d/%d skipped chunks",
                failed_chunks, total,
            )
        return all_segments


def _wav_duration_seconds(path: Path) -> float:
    try:
        import wave

        with wave.open(str(path), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            return frames / float(rate) if rate else 0.0
    except Exception:
        return 0.0
