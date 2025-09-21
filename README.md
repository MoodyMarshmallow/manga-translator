# Manga Translator

This repository contains a minimal Chrome extension and a FastAPI backend that work together to translate manga pages. The extension discovers large images on the active tab, sends them to the backend for OCR + translation, and overlays English text bubbles back on the page.

## Repository layout

```
.
├── extension/        # Manifest V3 Chrome extension source
│   ├── manifest.json
│   ├── content.js
│   └── sw.js
└── server/           # FastAPI backend that calls OCR + translation helpers
    ├── __init__.py
    ├── main.py
    ├── ocr.py
    ├── grouping.py
    └── translate.py
```

## Backend setup

1. Create a virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. (Optional) Configure external services:

   * **Google Cloud Vision** – install `google-cloud-vision` and provide credentials via `GOOGLE_APPLICATION_CREDENTIALS`.
   * **Cerebras** – install `cerebras-cloud-sdk` and export `CEREBRAS_API_KEY`.
   * **Gemini** – set `TRANSLATOR_PROVIDER=gemini`, install `requests` (included), and export `GEMINI_API_KEY` (optional `GEMINI_MODEL`).
    * **SciPy** – install `scipy` to use the KDTree implementation for grouping.

### Choosing a translation backend

`TRANSLATOR_PROVIDER` controls which API batches are sent to. It defaults to `cerebras`, which requires `CEREBRAS_API_KEY`. Set `TRANSLATOR_PROVIDER=gemini` to call Google’s Generative Language API with `GEMINI_API_KEY` (and optionally override `GEMINI_MODEL`). If the selected provider is unavailable, the server logs a warning and falls back to echoing the Korean text so the extension still renders overlays.

3. Run the API locally:

   ```bash
   uvicorn server.main:app --reload
   ```

   The extension expects the API at `http://localhost:8000` by default.

## Extension setup

1. Open `chrome://extensions/` and toggle **Developer mode**.
2. Choose **Load unpacked**, then select the `extension/` directory.
3. Navigate to a manga page and click the extension action to begin translation (the content script auto-runs on large images).

## Notes

* When the optional dependencies or API keys are not available, the backend returns placeholder translations so that the UI flow can still be exercised during development.
* This is an MVP implementation intended for hackathon demos. It keeps the file structure intentionally small and avoids additional UI chrome beyond the core translation overlay.
