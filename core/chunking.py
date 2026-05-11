from pathlib import Path
from typing import List, Tuple

from pydub import AudioSegment
from pydub.silence import split_on_silence


SHORT_ASR_TARGET_MS = 18 * 1000
SHORT_ASR_MAX_MS = 22 * 1000
SHORT_ASR_MIN_MS = 4 * 1000


def chunk_audio(
    audio_path: Path,
    min_chunk_ms: int = 5 * 60 * 1000,
    max_chunk_ms: int = 10 * 60 * 1000,
    silence_thresh: int = -40,
    min_silence_len: int = 500,
) -> List[Path]:
    """Split audio into chunks for processing.

    First tries to split on silence. If chunks are too small,
    falls back to fixed-length chunks with overlap.
    """
    audio = AudioSegment.from_file(str(audio_path))
    duration_ms = len(audio)

    if duration_ms <= max_chunk_ms:
        return [audio_path]

    chunks_dir = audio_path.parent / f"{audio_path.stem}_chunks"
    chunks_dir.mkdir(exist_ok=True)

    try:
        raw_chunks = split_on_silence(
            audio,
            min_silence_len=min_silence_len,
            silence_thresh=silence_thresh,
            keep_silence=200,
        )
    except Exception:
        raw_chunks = []

    if not raw_chunks or len(raw_chunks) < 2:
        return _fixed_chunks(audio, audio_path, chunks_dir, max_chunk_ms)

    merged = _merge_small_chunks(raw_chunks, min_chunk_ms, max_chunk_ms)

    chunk_paths = []
    for i, chunk in enumerate(merged):
        chunk_path = chunks_dir / f"chunk_{i:03d}.wav"
        chunk.export(str(chunk_path), format="wav")
        chunk_paths.append(chunk_path)

    return chunk_paths


def split_for_short_asr(
    audio_path: Path,
    target_ms: int = SHORT_ASR_TARGET_MS,
    max_ms: int = SHORT_ASR_MAX_MS,
    min_ms: int = SHORT_ASR_MIN_MS,
    silence_thresh: int = -40,
    min_silence_len: int = 400,
) -> List[Tuple[Path, float]]:
    """Split audio into pieces ≤22s for GigaAM short-form ``transcribe()``.

    Returns a list of ``(chunk_path, offset_seconds)`` where ``offset_seconds``
    is the start time of the chunk inside the original file. Used to
    reconstruct absolute timestamps from per-chunk transcriptions.

    The total duration of all chunks may be less than the original (silent
    regions are dropped) — that's fine because each chunk carries its own
    absolute offset.
    """
    audio = AudioSegment.from_file(str(audio_path))
    duration_ms = len(audio)

    chunks_dir = audio_path.parent / f"{audio_path.stem}_short_chunks"
    chunks_dir.mkdir(exist_ok=True)

    if duration_ms <= max_ms:
        single = chunks_dir / "chunk_000.wav"
        audio.export(str(single), format="wav")
        return [(single, 0.0)]

    pieces = _silence_aware_pieces(
        audio,
        target_ms=target_ms,
        max_ms=max_ms,
        min_ms=min_ms,
        silence_thresh=silence_thresh,
        min_silence_len=min_silence_len,
    )

    if not pieces:
        pieces = _fixed_window_pieces(audio, max_ms)

    # Drop pieces shorter than GigaAM's STFT window (n_fft=320 samples
    # @ 16 kHz = 20 ms). Anything below that crashes torchaudio with
    # ``stft expected 0 < n_fft < N``. We use a more conservative 100 ms
    # threshold — a chunk that short can't carry usable speech anyway.
    _MIN_CHUNK_MS = 100
    pieces = [(seg, off) for seg, off in pieces if len(seg) >= _MIN_CHUNK_MS]

    out: List[Tuple[Path, float]] = []
    for i, (segment, offset_ms) in enumerate(pieces):
        chunk_path = chunks_dir / f"chunk_{i:03d}.wav"
        segment.export(str(chunk_path), format="wav")
        out.append((chunk_path, offset_ms / 1000.0))
    return out


def _silence_aware_pieces(
    audio: AudioSegment,
    target_ms: int,
    max_ms: int,
    min_ms: int,
    silence_thresh: int,
    min_silence_len: int,
) -> List[Tuple[AudioSegment, int]]:
    """Detect silence and pack speech regions into ≤max_ms pieces.

    Returns ``[(segment, offset_ms_in_original), ...]``.
    """
    from pydub.silence import detect_nonsilent

    try:
        spans_ms = detect_nonsilent(
            audio,
            min_silence_len=min_silence_len,
            silence_thresh=silence_thresh,
        )
    except Exception:
        return []

    if not spans_ms:
        return []

    pieces: List[Tuple[AudioSegment, int]] = []
    cur_start: int = -1
    cur_end: int = -1

    def flush():
        nonlocal cur_start, cur_end
        if cur_start < 0:
            return
        seg = audio[cur_start:cur_end]
        if len(seg) >= min_ms or not pieces:
            pieces.append((seg, cur_start))
        else:
            prev_seg, prev_off = pieces[-1]
            merged = prev_seg + seg
            if len(merged) <= max_ms:
                pieces[-1] = (merged, prev_off)
            else:
                pieces.append((seg, cur_start))
        cur_start = -1
        cur_end = -1

    for span_start, span_end in spans_ms:
        span_len = span_end - span_start

        if span_len > max_ms:
            flush()
            for sub_start in range(span_start, span_end, max_ms):
                sub_end = min(sub_start + max_ms, span_end)
                pieces.append((audio[sub_start:sub_end], sub_start))
            continue

        if cur_start < 0:
            cur_start = span_start
            cur_end = span_end
            continue

        projected_len = span_end - cur_start
        if projected_len <= max_ms and (cur_end - cur_start) < target_ms:
            cur_end = span_end
        else:
            flush()
            cur_start = span_start
            cur_end = span_end

    flush()
    return pieces


def _fixed_window_pieces(
    audio: AudioSegment,
    chunk_ms: int,
) -> List[Tuple[AudioSegment, int]]:
    pieces: List[Tuple[AudioSegment, int]] = []
    pos = 0
    total = len(audio)
    while pos < total:
        end = min(pos + chunk_ms, total)
        pieces.append((audio[pos:end], pos))
        pos = end
    return pieces


def _merge_small_chunks(
    chunks: List[AudioSegment],
    min_ms: int,
    max_ms: int,
) -> List[AudioSegment]:
    """Merge chunks that are too small."""
    merged = []
    current = AudioSegment.empty()

    for chunk in chunks:
        if len(current) + len(chunk) <= max_ms:
            current += chunk
        else:
            if len(current) >= min_ms:
                merged.append(current)
            elif merged:
                merged[-1] += current
            current = chunk

    if len(current) > 0:
        if len(current) >= min_ms:
            merged.append(current)
        elif merged:
            merged[-1] += current
        else:
            merged.append(current)

    return merged


def _fixed_chunks(
    audio: AudioSegment,
    original_path: Path,
    chunks_dir: Path,
    chunk_ms: int,
    overlap_ms: int = 3000,
) -> List[Path]:
    """Split into fixed-length chunks with overlap."""
    chunk_paths = []
    start = 0
    i = 0

    while start < len(audio):
        end = min(start + chunk_ms, len(audio))
        chunk = audio[start:end]

        chunk_path = chunks_dir / f"chunk_{i:03d}.wav"
        chunk.export(str(chunk_path), format="wav")
        chunk_paths.append(chunk_path)

        start = end - overlap_ms if end < len(audio) else end
        i += 1

    return chunk_paths
