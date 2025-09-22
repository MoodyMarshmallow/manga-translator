# Comic Buddy Technology Map

```mermaid
flowchart LR
 subgraph Browser["Chrome Extension"]
        CS["content.js"]
        SW["sw.js service worker"]
        POP["popup UI"]
        STORAGE["chrome.storage (local + sync)"]
  end
 subgraph Backend["FastAPI Server"]
        ENTRY["server/main.py"]
        OCR["server/ocr.py"]
        GROUPING["server/grouping.py"]
        TRANSLATE["server/translate.py"]
        CONTEXT["ContextStore (JSON file)"]
        LOGGING["logging_config.py"]
  end
    User["Reader browsing webcomic"] --> CS
    CS -- "detects images >= 400x400, assigns ids" --> STORAGE
    CS -- REQUEST_ANALYZE --> SW
    POP -- toggle & retranslates --> CS
    POP -- state sync --> STORAGE
    SW -- fetch image adds Referer --> FETCH_API["Fetch API"]
    SW -- POST /analyze --> BACKEND_API["FastAPI backend"]
    BACKEND_API -. uvicorn .-> ENTRY
    ENTRY -- document_ocr --> OCR
    ENTRY -- group_words --> GROUPING
    ENTRY -- translate_groups_kr_to_en --> TRANSLATE
    TRANSLATE -- conversation history --> CONTEXT
    ENTRY -- response payload --> RESPONSE["JSON groups + bounding boxes"]
    OCR -- optional --> VISION["Google Cloud Vision API"]
    TRANSLATE -- "provider=cerebras" --> CEREBRAS["Cerebras Cloud SDK"]
    TRANSLATE -- "provider=gemini" --> GEMINI["Google Generative Language API"]
    RESPONSE --> SW
    SW -- ANALYZE_RESULT --> CS
    CS -- render overlays --> DOM["In-page overlays"]
    CS -- cache result keys src+size --> STORAGE
```

## System Flow
1. The reader loads a webcomic page; `content.js` watches for large images, tags each candidate with an id, and looks up cached results in `chrome.storage`.
2. When a new panel needs translation, `content.js` sends a `REQUEST_ANALYZE` message to the service worker, which fetches the image bytes (preserving the referer when required) and posts them to the FastAPI `/analyze` endpoint.
3. The FastAPI app (`server/main.py`) runs OCR, clusters word polygons, and invokes the translator adapter (Cerebras, Gemini, or the echo fallback). It writes the translated groups into the JSON response and records context for future consistency.
4. The service worker returns the `ANALYZE_RESULT` to `content.js`, which caches the payload, scales bounding boxes to the page, and renders overlays. The popup UI reads the same shared state to toggle the translator and trigger retranslations.
5. Subsequent visits reuse cached translations until the user forces a refresh, keeping the UI responsive even when APIs are unavailable.
