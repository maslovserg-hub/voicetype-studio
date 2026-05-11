import asyncio
import logging
import os
import urllib.request
from pathlib import Path

from .config import config

logger = logging.getLogger(__name__)

# Speaker voices available in silero v4_ru
_VALID_SPEAKERS = {"aidar", "baya", "kseniya", "xenia", "eugene"}


class TTSService:
    """Russian text-to-speech via Silero (Sber) v4_ru.

    We bypass ``torch.hub.load`` (its hubconf for silero is flaky — pulls the
    GitHub repo but doesn't always fetch the .pt model itself) and download
    the packaged model directly from silero's CDN. Subsequent calls reuse the
    cached file. Runs on CPU at roughly real-time speed; ~50 MB on disk.
    """

    _model = None
    # silero v4_ru's native model rate is 24 kHz. Asking for 48 kHz makes
    # the package linearly upsample the output, which adds the metallic /
    # rasp artefacts users notice — every voice ends up sounding tinny.
    # 24 kHz is the sweet spot: full model fidelity without the
    # interpolation noise. (8 kHz is the low-bandwidth telephone setting.)
    _sample_rate = 24000
    _MODEL_URL = "https://models.silero.ai/models/tts/ru/v4_ru.pt"

    @classmethod
    def _resolve_speaker(cls, override: str | None = None) -> str:
        """Pick a silero speaker, trying ``override`` first, then env, then
        the ``eugene`` default. ``override`` is what the caller passes in
        from ``Settings.tts_speaker``; reading the env directly is a legacy
        fallback for transcription-bot tests.
        """
        for candidate in (override, os.getenv("TTS_SPEAKER", "")):
            cleaned = (candidate or "").strip().lower()
            if cleaned in _VALID_SPEAKERS:
                return cleaned
            if cleaned:
                logger.warning(
                    "Unknown TTS speaker %r — ignoring. Valid: %s",
                    cleaned, sorted(_VALID_SPEAKERS),
                )
        return "eugene"

    @classmethod
    def _candidate_paths(cls) -> list[Path]:
        """Where to look for an already-downloaded silero model.

        Project location is checked first because it's guaranteed to be an
        ASCII path (``torch.package.PackageImporter`` chokes on paths
        containing non-ASCII characters like Cyrillic usernames). If the
        only cached copy is under ``~/.cache/silero/...Сергей...\\v4_ru.pt``
        we still pick it up — but :meth:`_ensure_model_downloaded` will
        copy it to the project dir before loading, see below.
        """
        paths = [config.silero_dir / "v4_ru.pt"]
        # Fallbacks: the conventional torch.hub / silero locations a previous
        # tool may have populated.
        home = Path.home()
        paths.extend([
            home / ".cache" / "silero" / "v4_ru.pt",
            home / ".cache" / "torch" / "hub" / "snakers4_silero-models_master"
                  / "src" / "silero" / "model" / "v4_ru.pt",
        ])
        return paths

    @classmethod
    def _model_path(cls) -> Path:
        cache_dir = config.silero_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / "v4_ru.pt"

    @classmethod
    def _ensure_model_downloaded(cls) -> Path:
        target = cls._model_path()
        if target.exists() and target.stat().st_size > 1_000_000:
            return target

        # Reuse a pre-existing copy from any standard location.
        for candidate in cls._candidate_paths()[1:]:  # skip target itself
            if candidate.exists() and candidate.stat().st_size > 1_000_000:
                # PackageImporter requires an ASCII path. If the candidate is
                # ASCII-clean we can just point at it; otherwise we copy it
                # over to the project dir (one-time, ~38 MB).
                if _is_ascii_path(candidate):
                    logger.info("Reusing silero model at %s", candidate)
                    return candidate
                logger.info(
                    "Found silero model at %s but path has non-ASCII chars; "
                    "copying to project location to satisfy PackageImporter.",
                    candidate,
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                import shutil
                shutil.copyfile(candidate, target)
                return target

        logger.info("Downloading silero v4_ru model from %s ...", cls._MODEL_URL)
        urllib.request.urlretrieve(cls._MODEL_URL, str(target))
        logger.info(
            "Silero model saved to %s (%.1f MB)",
            target, target.stat().st_size / 1024 / 1024,
        )
        return target

    @classmethod
    def _load(cls) -> None:
        if cls._model is not None:
            return

        import torch

        logger.info("Loading silero TTS model (v4_ru)...")
        path = cls._ensure_model_downloaded()
        cls._model = torch.package.PackageImporter(str(path)).load_pickle(
            "tts_models", "model"
        )
        cls._model.to("cpu")
        logger.info("Silero TTS model ready")

    @classmethod
    async def synthesize(
        cls,
        text: str,
        output_path: Path,
        speaker: str | None = None,
    ) -> Path:
        """Synthesize ``text`` into a WAV file at ``output_path``.

        ``speaker`` overrides the silero voice for this call (one of
        ``aidar`` / ``baya`` / ``kseniya`` / ``xenia`` / ``eugene``). The
        Settings UI passes ``settings.tts_speaker`` here; ``None`` falls
        back to the env var ``TTS_SPEAKER`` and finally to ``eugene``.

        Long inputs are split into sentence-sized chunks and concatenated —
        silero v4_ru is happiest under ~1000 chars per call.
        """
        loop = asyncio.get_event_loop()

        def _do() -> Path:
            cls._load()
            chosen = cls._resolve_speaker(speaker)

            chunks = _split_for_tts(text)
            if not chunks:
                raise ValueError("TTS got an empty text after cleanup")

            import torch

            audios = []
            for chunk in chunks:
                wav = cls._model.apply_tts(
                    text=chunk,
                    speaker=chosen,
                    sample_rate=cls._sample_rate,
                    put_accent=True,
                    put_yo=True,
                )
                audios.append(wav)

            full = torch.cat(audios) if len(audios) > 1 else audios[0]
            _save_wav_pcm16(full, cls._sample_rate, output_path)
            return output_path

        return await loop.run_in_executor(None, _do)


def _is_ascii_path(path: Path) -> bool:
    """torch.package.PackageImporter rejects non-ASCII Windows paths
    (like ``C:\\Users\\Сергей\\.cache\\...``) — guard before passing one in."""
    try:
        str(path).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _save_wav_pcm16(tensor, sample_rate: int, path: Path) -> None:
    """Save a 1D float32 audio tensor (range -1..1) as a 16-bit PCM WAV file.

    Bypasses torchaudio.save entirely — newer torchaudio (2.x) defaults to a
    ``torchcodec`` backend that isn't installed and refuses sox/soundfile
    fallbacks without explicit setup. Also normalizes peak amplitude to
    avoid clipping when silero returns values slightly above 1.0.
    """
    import wave

    import numpy as np

    arr = tensor.detach().cpu().numpy().astype(np.float32)
    peak = float(np.max(np.abs(arr))) if arr.size else 0.0
    if peak > 0:
        arr = arr / peak * 0.95
    arr_int16 = (arr * 32767.0).clip(-32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(arr_int16.tobytes())


_EMOJI_RE = __import__("re").compile(
    "["
    "\U0001F300-\U0001F6FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "]+",
    flags=__import__("re").UNICODE,
)


def _clean_for_tts(text: str) -> str:
    """Strip markdown / bullets / emoji / leftover punctuation so silero reads
    only natural Russian prose."""
    import re

    out_lines: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("##"):
            line = line.lstrip("#").strip()
            if not line.endswith((".", "!", "?", "…", ":")):
                line += "."
        elif line.startswith(("- ", "* ", "• ")):
            line = line[2:].strip()
        elif re.match(r"^\d+[.)]\s", line):
            line = re.sub(r"^\d+[.)]\s", "", line)

        line = _EMOJI_RE.sub("", line)
        # Smart-quote / dash normalization helps silero pronunciation.
        line = (
            line.replace("«", "")
                .replace("»", "")
                .replace("\"", "")
                .replace("—", "—")
                .replace(" ", " ")
        )
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            out_lines.append(line)

    cleaned = " ".join(out_lines)
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    return cleaned.strip()


def _split_for_tts(text: str, max_chars: int = 800) -> list[str]:
    """Clean the text, then pack sentences into <=max_chars chunks. Silero
    handles ~1k chars per call cleanly; longer inputs can produce truncated
    audio."""
    import re

    cleaned = _clean_for_tts(text)
    if not cleaned:
        return []

    sentences = re.split(r"(?<=[.!?…])\s+", cleaned)
    chunks: list[str] = []
    current = ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(current) + 1 + len(s) <= max_chars:
            current = f"{current} {s}".strip()
        else:
            if current:
                chunks.append(current)
            current = s
    if current:
        chunks.append(current)
    return chunks
