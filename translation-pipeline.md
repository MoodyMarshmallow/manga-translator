# Translation Pipeline

## Browser Extension Flow
- `extension/content.js` scans large images once they enter the DOM, assigns each a unique id, and sends an `ANALYZE_IMAGE` message containing the source URL, intrinsic dimensions, and referrer.
- The background service worker (`extension/sw.js`) fetches the image bytes (respecting the referrer), converts them into a base64 data URL, and calls the FastAPI backend at `POST /analyze` with the encoded image and a Korean language hint.
- On success the worker forwards the backend response back to the originating tab; the content script caches these results per image and triggers overlay rendering.

## Backend Pipeline (`server/main.py`)
1. **Input normalization** – `AnalyzeRequest.load_bytes` decodes base64 input (or optionally downloads from `image_url`) and returns raw image bytes.
2. **OCR extraction** – `document_ocr` (`server/ocr.py`) best-effort calls Google Cloud Vision `document_text_detection`. If the SDK or credentials are missing, it returns a single fallback word spanning the full frame with a placeholder message.
3. **Word grouping** – `group_words` (`server/grouping.py`) converts OCR polygons to boxes, builds a proximity graph (KDTree when SciPy is present, otherwise a naïve pass), finds connected components, and emits groups with bounding boxes, orientations, and indexes back into the OCR list.
4. **Text reconstruction** – for each group, the handler gathers the original OCR words, sorts them by orientation (vertical bubbles sorted right-to-left top-to-bottom, horizontal bubbles top-to-bottom left-to-right), and concatenates their text into `kr_text`.
5. **Translation** – `translate_groups_kr_to_en` (`server/translate.py`) orders groups left-to-right (column by column) and top-to-bottom before batching, then hands each JSON payload to the active provider (configured via `TRANSLATOR_PROVIDER`):
   - **Cerebras** – uses `llama-3.3-70b` through the Cerebras SDK when `cerebras.cloud.sdk` and `CEREBRAS_API_KEY` are present, respecting the shared rate limiter and structured JSON schema.
   - **Gemini** – calls the Google Generative Language API (`GEMINI_API_KEY`, optional `GEMINI_MODEL`) with the same payload, requesting JSON output and retrying on transient 429/5xxs.
   - When neither backend is available, it transparently echoes the Korean text so the extension still renders overlays.
6. **Response assembly** – the handler copies translations back onto each group, measures the image dimensions with Pillow, and returns `ocr_image_size` plus per-group metadata (`bbox`, `orientation`, `kr_text`, `en_text`).

## Overlay Rendering
- The content script recalculates overlay positions when the viewport changes, mapping each group’s OCR-space bounding box into page coordinates using the rendered image’s scale factor.
- Overlays display `en_text` (falling back to `kr_text`) and dynamically shrink their font size until the text fits inside the speech bubble’s rectangle.
- Resize and mutation observers ensure overlays stay aligned as images move, resize, or new panels appear.

## Failure Modes & Fallbacks
- Missing OCR dependencies or empty OCR results produce a placeholder group so the extension still renders a clear failure message.
- Translation failures log warnings and surface the untouched Korean text while respecting provider-specific throttling and retries.
