"""Yandex SpeechKit async recognition (API v3) for non-Russian speech.

GigaAM only knows Russian, and Gemini/OpenAI block Russian IPs — SpeechKit
works from Russia and detects the language itself (``auto``).

Flow: ffmpeg → OGG Opus 32 kbit/s mono (≈0.35 MB/min measured, so the
60 MB inline limit fits ~3 hours) → ``recognizeFileAsync`` with the
audio inline (no Object Storage bucket) → poll the operation → fetch the
result and turn final utterances into :class:`Segment`s.

Auth: API key of a service account with the ``ai.speechkit-stt.user`` role.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

import aiohttp

from .config import config
from .http import client_session
from .transcriber import Segment, Word

logger = logging.getLogger(__name__)

RECOGNIZE_URL = "https://stt.api.cloud.yandex.net/stt/v3/recognizeFileAsync"
RESULT_URL = "https://stt.api.cloud.yandex.net/stt/v3/getRecognition"
OPERATION_URL = "https://operation.api.cloud.yandex.net/operations/{id}"
CANCEL_URL = OPERATION_URL + ":cancel"

MAX_INLINE_BYTES = 60 * 1024 * 1024
POLL_INTERVAL_S = 3
MAX_WAIT_S = 60 * 60


async def transcribe(
    audio_path: Path,
    api_key: str,
    status_callback: Optional[Callable[[str], None]] = None,
) -> list[Segment]:
    """Recognize ``audio_path`` (any format ffmpeg reads) → segments."""
    api_key = (api_key or "").strip()
    if not api_key:
        raise RuntimeError(
            "Для языка «Другой» нужен API-ключ Yandex SpeechKit — "
            "добавьте его в Настройках."
        )

    ogg_path = config.temp_dir / f"speechkit_{uuid.uuid4().hex[:10]}.ogg"
    try:
        await _to_ogg_opus(audio_path, ogg_path)
        audio = ogg_path.read_bytes()
    finally:
        ogg_path.unlink(missing_ok=True)
    if len(audio) > MAX_INLINE_BYTES:
        raise RuntimeError(
            "Запись слишком длинная для SpeechKit (больше ~3 часов)."
        )

    headers = {"Authorization": f"Api-Key {api_key}"}
    payload = {
        "content": base64.b64encode(audio).decode("ascii"),
        "recognitionModel": {
            "model": "general",
            "audioFormat": {"containerAudio": {"containerAudioType": "OGG_OPUS"}},
            "textNormalization": {
                "textNormalization": "TEXT_NORMALIZATION_ENABLED",
                "literatureText": True,
            },
            "languageRestriction": {
                "restrictionType": "WHITELIST",
                "languageCode": ["auto"],
            },
        },
    }

    timeout = aiohttp.ClientTimeout(total=300)
    async with client_session(timeout=timeout) as session:
        async with session.post(RECOGNIZE_URL, headers=headers, json=payload) as resp:
            body = await resp.text()
            if resp.status != 200:
                logger.error("speechkit recognize %s: %s", resp.status, body[:500])
                raise RuntimeError(f"SpeechKit вернул {resp.status}: {body[:200]}")
        op_id = json.loads(body)["id"]
        logger.info("speechkit operation %s (%d KB audio)", op_id, len(audio) // 1024)

        try:
            await _poll(session, headers, op_id, status_callback)
        except asyncio.CancelledError:
            # ⏹ Стоп: ask Yandex to drop the job. Best effort — the
            # operation may not support cancelling, and we don't wait long.
            try:
                await asyncio.wait_for(_cancel(session, headers, op_id), timeout=5)
            except Exception:
                logger.debug("speechkit cancel %s failed", op_id, exc_info=True)
            raise

        async with session.get(
            RESULT_URL, headers=headers, params={"operationId": op_id}
        ) as resp:
            body = await resp.text()
            if resp.status != 200:
                logger.error("speechkit result %s: %s", resp.status, body[:500])
                raise RuntimeError(f"SpeechKit вернул {resp.status}: {body[:200]}")

    return parse_recognition(body)


async def _poll(session, headers, op_id, status_callback) -> None:
    """Wait until the recognition operation is done."""
    started = time.monotonic()
    while True:
        await asyncio.sleep(POLL_INTERVAL_S)
        elapsed = int(time.monotonic() - started)
        if status_callback:
            status_callback(f"Распознаю в Яндексе… {elapsed // 60}:{elapsed % 60:02d}")
        async with session.get(
            OPERATION_URL.format(id=op_id), headers=headers
        ) as resp:
            op = await resp.json(content_type=None)
        if op.get("error"):
            raise RuntimeError(f"SpeechKit: {op['error'].get('message', op['error'])}")
        if op.get("done"):
            return
        if elapsed > MAX_WAIT_S:
            raise RuntimeError("SpeechKit не ответил за час — попробуйте позже.")


async def _cancel(session, headers, op_id) -> None:
    async with session.post(CANCEL_URL.format(id=op_id), headers=headers) as resp:
        logger.info("speechkit cancel %s: %s %s", op_id, resp.status, (await resp.text())[:200])


def parse_recognition(body: str) -> list[Segment]:
    """``getRecognition`` body → segments.

    The body is a stream of JSON objects (``{"result": {...}}`` each).
    Every utterance comes twice: raw ``final`` and
    ``finalRefinement.normalizedText`` — prefer the latter, fall back to
    ``final`` if there are no refinements. Punctuation is Russian-only and
    skipped entirely in ``auto`` mode, so foreign text arrives unpunctuated.
    """
    raw: list[Segment] = []
    refined: list[Segment] = []
    for obj in _iter_json_objects(body):
        result = obj.get("result", obj)
        if str(result.get("channelTag", "0")) != "0":
            continue
        if "finalRefinement" in result:
            alts = (result["finalRefinement"].get("normalizedText") or {}).get("alternatives")
            target = refined
        elif "final" in result:
            alts = result["final"].get("alternatives")
            target = raw
        else:
            continue
        if not alts:
            continue
        alt = alts[0]
        text = (alt.get("text") or "").strip()
        if not text:
            continue
        # One utterance can span the whole recording; per-word timings let
        # the Formatter cut it into short timestamped lines.
        words = [
            Word(
                start=int(w.get("startTimeMs", 0)) / 1000.0,
                end=int(w.get("endTimeMs", 0)) / 1000.0,
                text=w.get("text", ""),
            )
            for w in alt.get("words") or []
            if w.get("text")
        ]
        target.append(Segment(
            start=int(alt.get("startTimeMs", 0)) / 1000.0,
            end=int(alt.get("endTimeMs", 0)) / 1000.0,
            text=text,
            words=words,
        ))
    return refined or raw


def _iter_json_objects(body: str):
    """Yield consecutive JSON values from newline/concatenated JSON."""
    decoder = json.JSONDecoder()
    i, n = 0, len(body)
    while i < n:
        while i < n and body[i] in " \t\r\n,":
            i += 1
        if i >= n:
            break
        value, i = decoder.raw_decode(body, i)
        if isinstance(value, list):
            yield from (v for v in value if isinstance(v, dict))
        elif isinstance(value, dict):
            yield value


async def _to_ogg_opus(src: Path, dst: Path) -> None:
    cmd = [
        "ffmpeg", "-i", str(src), "-vn",
        "-ac", "1", "-c:a", "libopus", "-b:a", "32k",
        "-y", str(dst),
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await process.communicate()
    except asyncio.CancelledError:
        process.kill()  # ⏹ Стоп — the caller's finally removes ``dst``
        await process.wait()
        raise
    if process.returncode != 0:
        raise RuntimeError(
            f"ffmpeg не смог сжать аудио: {stderr.decode(errors='replace')[-300:]}"
        )
