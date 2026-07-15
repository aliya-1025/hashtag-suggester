"""
Hashtag Suggester - Backend
----------------------------
Flow:
1. User gives a YouTube URL OR uploads a video/audio file.
2. We extract audio (yt-dlp for YouTube, ffmpeg for uploaded files) and
   transcribe it locally with faster-whisper (no per-minute API cost).
3. We send the transcript + the user's hashtag bank to Claude, which
   picks the most relevant hashtags from the bank (ranked, with a short
   reason) and optionally suggests a few new ones not in the bank.

Run locally:
    pip install -r requirements.txt --break-system-packages
    cp .env.example .env   # add your ANTHROPIC_API_KEY
    uvicorn main:app --reload --port 8000
"""

import os
import json
import tempfile
import shutil
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import anthropic

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")  # tiny/base/small - base is a good CPU default

app = FastAPI(title="Hashtag Suggester API")

# Allow the frontend (any origin during dev; lock this down to your Vercel
# domain once deployed, e.g. allow_origins=["https://your-app.vercel.app"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

# Lazy-load whisper model once, on first use, so the server boots fast.
_whisper_model = None


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        # int8 keeps this usable on CPU / weaker GPUs
        _whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    return _whisper_model


def transcribe_audio(audio_path: str) -> str:
    model = get_whisper_model()
    segments, _info = model.transcribe(audio_path, beam_size=5)
    return " ".join(seg.text.strip() for seg in segments)


def download_youtube_audio(youtube_url: str, out_dir: str) -> str:
    """Downloads best audio from a YouTube URL and returns the local file path."""
    import yt_dlp

    out_template = str(Path(out_dir) / "audio.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "quiet": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([youtube_url])

    audio_path = Path(out_dir) / "audio.mp3"
    if not audio_path.exists():
        raise HTTPException(status_code=422, detail="Couldn't extract audio from that YouTube URL.")
    return str(audio_path)


def extract_audio_from_upload(upload_path: str, out_dir: str) -> str:
    """Uses ffmpeg to pull a clean mp3 out of any uploaded video/audio file."""
    import subprocess

    audio_path = str(Path(out_dir) / "audio.mp3")
    cmd = ["ffmpeg", "-y", "-i", upload_path, "-vn", "-acodec", "libmp3lame", audio_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not Path(audio_path).exists():
        raise HTTPException(status_code=422, detail="Couldn't extract audio from the uploaded file.")
    return audio_path


def ask_claude_for_hashtags(transcript: str, hashtag_bank: List[str]) -> dict:
    if client is None:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not set on the server.")

    bank_text = ", ".join(hashtag_bank)

    system_prompt = (
        "You are a social media strategist who picks hashtags for YouTube videos. "
        "You will be given a video transcript and a bank of hashtags the creator already uses. "
        "Return ONLY valid JSON, no markdown fences, no preamble, matching this shape:\n"
        "{\n"
        '  "matched_hashtags": [{"tag": "#example", "reason": "short reason, under 12 words"}],\n'
        '  "new_suggestions": [{"tag": "#example", "reason": "short reason, under 12 words"}]\n'
        "}\n"
        "Rules:\n"
        "- matched_hashtags: only tags that exist in the given bank, ranked most to least relevant. "
        "Include every bank tag that is genuinely relevant, not a fixed count.\n"
        "- new_suggestions: 3-5 extra hashtags NOT in the bank that would help this specific video "
        "reach the right audience. These must be clearly relevant to the transcript's actual content.\n"
        "- Keep every tag lowercase, no spaces, starting with #.\n"
    )

    user_prompt = (
        f"HASHTAG BANK:\n{bank_text}\n\n"
        f"VIDEO TRANSCRIPT:\n{transcript[:12000]}\n\n"
        "Pick the matching hashtags and suggest new ones per the rules."
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw_text = "".join(block.text for block in response.content if block.type == "text").strip()
    raw_text = raw_text.replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail=f"Claude didn't return valid JSON: {raw_text[:300]}")


@app.get("/api/health")
def health():
    return {"status": "ok", "whisper_model": WHISPER_MODEL_SIZE, "claude_configured": client is not None}


@app.post("/api/analyze")
async def analyze(
    hashtag_bank: str = Form(..., description="Comma or newline separated hashtags"),
    youtube_url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    if not youtube_url and not file:
        raise HTTPException(status_code=400, detail="Provide either a youtube_url or a file.")

    bank_list = [
        (t if t.startswith("#") else f"#{t}")
        for t in [x.strip() for x in hashtag_bank.replace("\n", ",").split(",")]
        if t.strip()
    ]
    if not bank_list:
        raise HTTPException(status_code=400, detail="Hashtag bank is empty.")

    with tempfile.TemporaryDirectory() as tmp_dir:
        if youtube_url:
            audio_path = download_youtube_audio(youtube_url, tmp_dir)
        else:
            upload_path = str(Path(tmp_dir) / file.filename)
            with open(upload_path, "wb") as f:
                shutil.copyfileobj(file.file, f)
            audio_path = extract_audio_from_upload(upload_path, tmp_dir)

        transcript = transcribe_audio(audio_path)

    if not transcript.strip():
        raise HTTPException(status_code=422, detail="Got no speech from that video.")

    result = ask_claude_for_hashtags(transcript, bank_list)
    result["transcript_preview"] = transcript[:500]
    return result
