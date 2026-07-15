# Tagbank — video → hashtag suggester

Give it a YouTube URL (ya video/audio file), yeh Whisper se sunke transcript
banata hai, phir Claude se tumhare hashtag bank mese sabse relevant hashtags
match karwata hai — plus kuch naye suggestions jo bank me nahi hai.

## How it works

1. **backend/** — FastAPI server
   - YouTube URL → `yt-dlp` audio download
   - Uploaded file → `ffmpeg` se audio extract
   - Audio → transcript via `faster-whisper` (local, free, CPU par chal jata hai)
   - Transcript + hashtag bank → Claude API → ranked matched hashtags + new ideas
2. **frontend/** — single `index.html`, no build step. Directly Netlify Drop ya
   Vercel pe deploy ho sakta hai (jaise tumhara Father's Day site tha).

## Local setup

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt --break-system-packages
cp .env.example .env            # isme apna ANTHROPIC_API_KEY daalo
uvicorn main:app --reload --port 8000
```

**Important — ffmpeg required:** yt-dlp aur upload dono ko ffmpeg chahiye.
- Windows: https://ffmpeg.org se download karke PATH me add karo
- (tumhare Windows PC pe pehle npm issue hua tha — agar PATH issue aaye,
  same tarah directly ffmpeg.exe ka full path use kar sakti ho)

First request thoda slow hoga kyuki whisper model (`base`, ~140MB) pehli
baar download hota hai. Chhoti videos ke liye `base` theek hai; better
accuracy chahiye to `.env` me `WHISPER_MODEL_SIZE=small` kar do (slower).

### Frontend

Bas `frontend/index.html` ko browser me kholo. Agar backend kisi doosre URL
pe chal raha hai (deployed), to browser console me yeh set kar do ya file
me top pe ek line add kar do:

```html
<script>window.TAGBANK_API_BASE = "https://your-backend.onrender.com";</script>
```

(index.html ke `<script>` tag se pehle add karo)

## Deploying (same workflow jo attendance apps me use kiya tha)

### Backend → Render
1. Is `backend/` folder ko GitHub repo me push karo
2. Render pe naya Web Service banao, root directory = `backend`
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Environment variable add karo: `ANTHROPIC_API_KEY`
6. **Note:** Render free tier CPU whisper ke liye slow ho sakta hai for
   long videos — agar timeout aaye, `WHISPER_MODEL_SIZE=tiny` try karo,
   ya paid tier pe upgrade karo.

### Frontend → Vercel
1. `frontend/` folder ko GitHub pe push karo (ya seedha Vercel/Netlify Drop)
2. Deploy karte waqt `index.html` me `TAGBANK_API_BASE` ko apne live
   Render URL se update kar do

Push → auto redeploy, jaisa tumhara existing workflow hai.

## Notes / limitations

- yt-dlp kabhi kabhi YouTube ke side se rate-limited ho sakta hai agar
  bahut zyada requests ek saath jaayein — team use ke liye keep an eye.
- Bahut lambi videos (1hr+) transcribe hone me time lagega on CPU —
  agar zaroorat pade to GPU wale machine/host pe move kar sakte ho.
- Claude ko transcript ka pehla ~12,000 characters bhejta hai (bahut
  lambe transcripts ke liye) — matching ke liye generally kaafi hai.
