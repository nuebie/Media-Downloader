import os
from dotenv import load_dotenv
import shutil
import tempfile

from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import yt_dlp

load_dotenv()

app = FastAPI(title="Self-hosted video downloader")

# Set this to a long random string as an environment variable on the server.
# n8n must send the same value in the x-api-key header on every request,
# or the request is rejected. Without this, anyone who finds your server's
# address could use it to download videos through your server/IP.
API_KEY = os.environ.get("DOWNLOADER_API_KEY", "")

# Optional: route yt-dlp's requests through a proxy (e.g. a residential
# proxy), in case the android-client fix alone isn't enough for a given
# video. Leave unset to make requests directly, same as before.
PROXY_URL = os.environ.get("PROXY_URL", "").strip()


class DownloadRequest(BaseModel):
    url: str


@app.post("/download")
def download(payload: DownloadRequest, x_api_key: str = Header(default=None)):
    if not API_KEY or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Missing or invalid x-api-key header")

    url = payload.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    work_dir = tempfile.mkdtemp(prefix="dl_")
    outtmpl = os.path.join(work_dir, "%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        # Prefer a single progressive mp4 (no merge needed). Fall back to
        # best video + best audio, merged with ffmpeg, for sites that only
        # offer split streams at higher quality (this is common on YouTube).
        "format": "mp4[vcodec!=none][acodec!=none]/bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }

    if PROXY_URL:
        ydl_opts["proxy"] = PROXY_URL

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)
            # merge_output_format can change the extension after download.
            if not os.path.exists(filepath):
                base, _ = os.path.splitext(filepath)
                candidate = base + ".mp4"
                if os.path.exists(candidate):
                    filepath = candidate
    except yt_dlp.utils.DownloadError as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"Download failed: {e}")
    except Exception as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")

    if not os.path.exists(filepath):
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail="Download finished but the output file was not found")

    filename = os.path.basename(filepath)

    def stream_and_cleanup():
        try:
            with open(filepath, "rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    yield chunk
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    return StreamingResponse(
        stream_and_cleanup(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/health")
def health():
    return {"status": "ok"}