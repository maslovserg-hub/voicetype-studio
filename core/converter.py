import asyncio
import subprocess
from pathlib import Path
from typing import Optional


class AudioConverter:
    TARGET_SAMPLE_RATE = 16000
    TARGET_CHANNELS = 1

    @classmethod
    async def to_wav(cls, input_path: Path, output_path: Optional[Path] = None) -> Path:
        """Convert audio/video to WAV format suitable for ASR."""
        if output_path is None:
            output_path = input_path.with_suffix(".wav")

        if output_path.exists():
            output_path.unlink()

        cmd = [
            "ffmpeg",
            "-i", str(input_path),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", str(cls.TARGET_SAMPLE_RATE),
            "-ac", str(cls.TARGET_CHANNELS),
            "-y",
            str(output_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()

        if process.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {stderr.decode()}")

        return output_path

    @classmethod
    async def get_duration(cls, file_path: Path) -> float:
        """Get audio/video duration in seconds."""
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(file_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()

        try:
            return float(stdout.decode().strip())
        except ValueError:
            return 0.0
