import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, List

from .transcriber import Segment, Word

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")
_SENTENCE_END = re.compile(r"[.!?…]")
_PARAGRAPH_EVERY = 4

# Subtitle / line-grouping limits — keep blocks short enough to read on screen
# and to avoid the "wall of text" problem when the source segments are 20s+.
_MAX_LINE_DURATION_S = 6.0
_MAX_LINE_CHARS = 80


class OutputFormat(Enum):
    TEXT = "text"
    TIMESTAMPS = "timestamps"
    SRT = "srt"


class Formatter:
    @staticmethod
    def format(segments: List[Segment], fmt: OutputFormat) -> str:
        if fmt == OutputFormat.TEXT:
            return Formatter._to_text(segments)
        elif fmt == OutputFormat.TIMESTAMPS:
            return Formatter._to_timestamps(segments)
        elif fmt == OutputFormat.SRT:
            return Formatter._to_srt(segments)
        else:
            return Formatter._to_text(segments)

    @staticmethod
    def _to_text(segments: List[Segment]) -> str:
        """Plain text output, one sentence per line + blank line every few sentences."""
        raw = " ".join(seg.text for seg in segments if seg.text).strip()
        if not raw:
            return ""

        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(raw) if s.strip()]
        if len(sentences) <= 1:
            return raw

        lines: List[str] = []
        for i, sent in enumerate(sentences):
            lines.append(sent)
            if (i + 1) % _PARAGRAPH_EVERY == 0 and i + 1 < len(sentences):
                lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _to_timestamps(segments: List[Segment]) -> str:
        """Text with timestamps. Uses word-level timing to break into short
        readable lines (~80 chars / ~6 sec each). Falls back to per-segment
        block if word timings aren't available."""
        lines: List[str] = []
        for block in _phrase_blocks(segments):
            ts = _format_time(block.start)
            lines.append(f"[{ts}] {block.text}")
        return "\n".join(lines)

    @staticmethod
    def _to_srt(segments: List[Segment]) -> str:
        """SRT subtitle format. Splits into short subtitle entries based on
        sentence punctuation and ~6 sec / ~80 chars limits — gives readable
        on-screen subtitles instead of one 20-second wall of text per chunk."""
        lines: List[str] = []
        for i, block in enumerate(_phrase_blocks(segments), 1):
            start = _format_srt_time(block.start)
            end = _format_srt_time(block.end)
            lines.append(str(i))
            lines.append(f"{start} --> {end}")
            lines.append(block.text)
            lines.append("")
        return "\n".join(lines)


@dataclass
class _Block:
    start: float
    end: float
    text: str


def _phrase_blocks(segments: List[Segment]) -> List[_Block]:
    """Walk through segments, gather short readable blocks suitable for
    subtitles or timestamped lines."""
    blocks: List[_Block] = []

    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue

        # Word timings let us break inside the segment by sentence/length.
        if seg.words:
            blocks.extend(_blocks_from_words(seg.words))
            continue

        # Fallback: no word timings → keep the segment as one block (legacy).
        blocks.append(_Block(start=seg.start, end=seg.end, text=text))

    return blocks


def _blocks_from_words(words: Iterable[Word]) -> List[_Block]:
    out: List[_Block] = []
    cur_words: List[Word] = []

    def flush():
        if not cur_words:
            return
        text = " ".join(w.text for w in cur_words).strip()
        if text:
            out.append(_Block(start=cur_words[0].start, end=cur_words[-1].end, text=text))
        cur_words.clear()

    for w in words:
        cur_words.append(w)
        cur_text = " ".join(x.text for x in cur_words)
        duration = cur_words[-1].end - cur_words[0].start

        ends_sentence = bool(_SENTENCE_END.search(w.text))
        too_long = duration >= _MAX_LINE_DURATION_S or len(cur_text) >= _MAX_LINE_CHARS

        if ends_sentence or too_long:
            flush()

    flush()
    return out


def _format_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_srt_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
