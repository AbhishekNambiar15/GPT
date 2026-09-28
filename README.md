# MiniGPT

Flask web application with Gemini chat, YouTube music search/playback, and API usage tracking.

## Requirements

- Python
- Dependencies in `requirements.txt`:
  - `Flask`
  - `python-dotenv`

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Configure environment variables in `.env` (refer to `.env.example`):
   ```env
   GEMINI_API_KEY=your_gemini_api_key
   GEMINI_MODEL=gemini-3.8-flash
   YOUTUBE_API_KEY=your_youtube_api_key

   GEMINI_DAILY_LIMIT=1500
   YOUTUBE_DAILY_LIMIT=10000
   API_WARNING_THRESHOLD=80
   API_CRITICAL_THRESHOLD=90
   ```

3. Run the app:
   ```bash
   python app.py
   ```

4. Open in browser:
   `http://localhost:5000`

## Files

- `app.py`: Flask server and API routes (`/api/chat`, `/api/youtube/search`, `/api/youtube/related`, `/api/usage`, `/api/stats`).
- `api_manager.py`: API usage tracking, limits, and response caching.
- `index.html`: Chat page and API usage card.
- `music.html`: Music search and player page.
- `settings.html`: User settings and API usage display.
- `memory.html`: Notes and saved memory items.
- `modes.html`: Modes selection page.
- `tools.html`: Tools page.
- `welcome.html`: Initial welcome and name entry page.
- `player.js`: Music player and autoplay logic.
- `shared.css`: Global styles.
