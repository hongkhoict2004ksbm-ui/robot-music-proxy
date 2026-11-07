
import logging
import os
import shutil
import subprocess
from typing import Iterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from yt_dlp import YoutubeDL

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO")
)
logger = logging.getLogger("web_music_proxy")

app = FastAPI(
    title="ESP32 Web Music Proxy",
    version="2.0.0"
)

SAMPLE_RATE = 24000
CHANNELS = 1
CHUNK_SIZE = 4096

def build_ydl_options() -> dict:
    opts = {
        # Mở rộng dải chọn format để tránh lỗi "Requested format is not available"
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
        "default_search": "ytsearch",
        "extract_flat": False,
        "socket_timeout": 20,
        "retries": 3,
        "fragment_retries": 3,
        "cookiefile": "cookies.txt",
    }
    return opts
def stream_pcm_generator(
    audio_url: str,
    http_headers: dict | None = None
) -> Iterator[bytes]:

    if not shutil.which("ffmpeg"):
        logger.error("FFmpeg not found")
        return

    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error"
    ]

    if http_headers:
        header_blob = "".join(
            f"{key}: {value}\r\n"
            for key, value in http_headers.items()
        )

        if header_blob:
            command += [
                "-headers",
                header_blob
            ]

    command += [
        "-i", audio_url,
        "-vn",
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "-ar", str(SAMPLE_RATE),
        "-ac", str(CHANNELS),
        "pipe:1"
    ]

    process = None

    try:
        logger.info(
            "Starting FFmpeg: %d Hz mono PCM",
            SAMPLE_RATE
        )

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0
        )

        assert process.stdout is not None

        while True:
            chunk = process.stdout.read(CHUNK_SIZE)

            if not chunk:
                break

            yield chunk

        return_code = process.wait(timeout=10)

        if return_code != 0:
            logger.error(
                "FFmpeg exited with code %s",
                return_code
            )

    except GeneratorExit:
        logger.info("Client disconnected")
        raise

    except Exception:
        logger.exception("PCM streaming error")

    finally:
        if process is not None:

            if process.poll() is None:
                process.terminate()

                try:
                    process.wait(timeout=3)

                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

            if process.stdout:
                process.stdout.close()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "ffmpeg_available": (
            shutil.which("ffmpeg") is not None
        ),
        "po_token_provider_configured": bool(
            os.getenv("BGUTIL_POT_BASE_URL", "").strip()
        )
    }


@app.get("/play")
def play_music(
    q: str = Query(
        ...,
        min_length=1,
        max_length=300
    )
):

    query = q.strip()

    if not query:
        raise HTTPException(
            status_code=400,
            detail="Missing search query"
        )

    logger.info(
        "Music search requested: %s",
        query
    )

    try:

        with YoutubeDL(build_ydl_options()) as ydl:

            info = ydl.extract_info(
                f"ytsearch1:{query}",
                download=False
            )

            entries = (info or {}).get("entries") or []

            video = next(
                (item for item in entries if item),
                None
            )

            if not video:
                raise HTTPException(
                    status_code=404,
                    detail="No matching music found"
                )

            audio_url = video.get("url")

            if not audio_url:
                raise HTTPException(
                    status_code=502,
                    detail="No audio URL returned"
                )

            title = video.get(
                "title",
                "unknown"
            )

            headers = video.get(
                "http_headers"
            ) or {}

            logger.info(
                "Selected audio: %s",
                title
            )

        return StreamingResponse(
            stream_pcm_generator(
                audio_url,
                headers
            ),
            media_type="audio/pcm",
            headers={
                "Cache-Control": "no-store",
                "X-Audio-Format": "s16le",
                "X-Audio-Sample-Rate": str(SAMPLE_RATE),
                "X-Audio-Channels": str(CHANNELS)
            }
        )

    except HTTPException:
        raise

    except Exception as exc:

        logger.exception(
            "yt-dlp failed: %s",
            query
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Music source could not be resolved: "
                + type(exc).__name__
            )
        ) from exc


if __name__ == "__main__":

    import uvicorn

    port = int(
        os.getenv("PORT", "10000")
    )

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port
    )
