Here’s a **ship-ready technical spec** for your Chrome extension, built exactly the way you described:

* **Scrape manga images in-page**
* **Send images to a Python backend**
* **Python does OCR with Google Cloud Vision**
* **Group word boxes with a KD‑Tree**
* **Translate via a Cerebras LLM**
* **Overlay English text back onto the page** (DIVs positioned over original bubbles)

I’ll keep the prose light, the details heavy, and flag uncertainties where they exist.

---

## 0) High-level architecture

```
[Chrome Extension (MV3)]
  ├─ Content script: finds images, geometry (DOMRect + natural size), injects overlays
  ├─ Service worker: fetches image bytes (cross-origin), talks to Python API
  └─ (optional) Popup: toggle, settings

[Python API (FastAPI)]
  ├─ /analyze (POST): OCR -> KD-Tree grouping -> LLM translation -> return groups
  ├─ /health (GET)
  └─ Auth, logging, rate limiting
```

**Why these pieces?**

* **MV3** + **host permissions** lets your service worker make cross‑origin requests (e.g., to your Python API) cleanly and avoid CORS headaches for most cases. ([Chrome for Developers][1])
* **Accurate overlays** come from mapping **image-intrinsic px** (Vision’s bounding boxes) to **on-page CSS px** using `getBoundingClientRect()` and `naturalWidth/Height`. ([MDN Web Docs][2])
* **Vision OCR** returns lines/words **with bounding polygons**; `DOCUMENT_TEXT_DETECTION` is the robust mode for dense text (manga pages). ([Google Cloud][3])
* **KD-Tree** gives fast nearest-neighbor grouping into speech “bubble-ish” clusters. Use SciPy/Scikit‑learn KDTree. ([SciPy Documentation][4])
* **Cerebras** offers a simple **Chat Completions** API with a Python SDK; you can enforce JSON-shaped outputs (`response_format`) for reliable post-processing. ([Cerebras Inference][5])

---

## 1) Chrome extension spec (Manifest V3)

### 1.1 Manifest

```json
{
  "manifest_version": 3,
  "name": "Manga Translator Overlay",
  "version": "0.1.0",
  "permissions": ["activeTab", "scripting", "storage"],
  "host_permissions": ["https://your-api.example.com/*", "*://*/*"],
  "background": { "service_worker": "sw.js" },
  "content_scripts": [{
    "matches": ["<all_urls>"],
    "js": ["content.js"],
    "run_at": "document_idle"
  }],
  "action": { "default_title": "Translate manga" }
}
```

* **host\_permissions** enable cross-origin network requests (service worker) to your API. ([Chrome for Developers][1])
* MV3 service worker replaces MV2 background pages. ([Chrome for Developers][6])

### 1.2 Content script (core responsibilities)

* **Discover candidate images**: large `<img>` elements (and optionally CSS background images).
* **Measure geometry**: `img.getBoundingClientRect()`, plus `img.naturalWidth/Height` (intrinsic). ([MDN Web Docs][2])
* **Send work items** to service worker: `{imageSrc, rect, naturalSize}`. Use `chrome.runtime.sendMessage` / `chrome.tabs.sendMessage` patterns. ([Chrome for Developers][7])
* **Overlay results**: receive grouped boxes + translations, compute on‑page positions, inject absolutely positioned `<div>`s.
* **Keep overlays in sync** with layout changes via `ResizeObserver` (image resizes), `IntersectionObserver` (visibility), and `MutationObserver` (DOM changes). ([MDN Web Docs][8])

**Mapping image-px → CSS-px**:

```js
const rect = img.getBoundingClientRect();      // viewport coords
const scaleX = rect.width  / img.naturalWidth; // intrinsic → displayed
const scaleY = rect.height / img.naturalHeight;
const pageX = rect.left + window.scrollX;      // convert to page coords
const pageY = rect.top  + window.scrollY;
```

* `getBoundingClientRect()` is viewport-relative; add scroll offsets for absolute positioning. ([MDN Web Docs][2])

**Overlay style basics**:

```css
.manga-translate {
  position: absolute;
  pointer-events: none;
  background: rgba(255,255,255,0.88);
  border-radius: 10px;
  padding: 6px 8px;
  font: 600 14px/1.35 system-ui, -apple-system, "Segoe UI", Arial, sans-serif;
  color: #111;
  text-shadow: 0 1px 0 rgba(255,255,255,0.4);
}
```

### 1.3 Service worker (`sw.js`)

* **Listens** for messages from `content.js`.
* **Fetches image bytes** (avoid canvas taint) and calls your Python API. Cross-origin requests from the service worker are permitted with host permissions. ([Chrome for Developers][1])
* **Relays results** back to the correct tab via `chrome.tabs.sendMessage`. ([Chrome for Developers][7])

> **CORS gotcha:** content scripts **are** subject to page CORS; move network calls to the service worker with proper host permissions. ([Chrome for Developers][1])
> **Logged-in/behind-auth images:** if the source needs site cookies, background fetch may not have them. For v1, **scope to public images**; as a fallback, you can rasterize the **visible viewport** via `chrome.tabs.captureVisibleTab()` and OCR that, at some quality cost. ([MDN Web Docs][9])

---

## 2) Python service spec (FastAPI)

### 2.1 Environment & dependencies

```bash
pip install fastapi uvicorn google-cloud-vision numpy scipy scikit-learn pillow \
            cerebras-cloud-sdk pydantic
```

* **Vision client** for OCR. ([GitHub][10])
* **Cerebras SDK** for Chat Completions. (Install via PyPI `cerebras-cloud-sdk`.) ([PyPI][11])

**Credentials (security)**

* **Do not** ship Google service-account keys or any API keys in the extension—client code is inspectable. Keep secrets server-side and use env vars on the server. ([Google Cloud][12])

### 2.2 API surface

* `POST /analyze`
  **Request**:

  ```json
  {
    "image_url": "https://...",         // or "image_b64": "data:image/png;base64,..."
    "intrinsic_size": {"w": 2480, "h": 3508}, // from img.naturalWidth/Height
    "language_hint": "ja"
  }
  ```

  **Response**:

  ```json
  {
    "ocr_image_size": {"w": 2480, "h": 3508},
    "groups": [
      {
        "id": "g_12",
        "bbox": {"x0": 812, "y0": 420, "x1": 1140, "y1": 1026},
        "orientation": "vertical" | "horizontal",
        "jp_text": "...",
        "en_text": "..."
      }
    ]
  }
  ```

* `GET /health` → `{status:"ok"}`

### 2.3 OCR (Google Cloud Vision) — Python

Use **`DOCUMENT_TEXT_DETECTION`** for dense pages. It returns a hierarchical structure (pages → blocks → paragraphs → words → symbols) with **bounding boxes** for each level. ([Google Cloud][3])

```python
# server/ocr.py
from google.cloud import vision

def document_ocr(image_bytes: bytes, language_hint: str = "ja"):
    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=image_bytes)
    # languageHints is optional; Vision auto-detects many languages
    # but hints can help with Japanese. :contentReference[oaicite:19]{index=19}
    params = vision.TextDetectionParams()  # (left default)
    image_context = vision.ImageContext(
        language_hints=[language_hint] if language_hint else None
    )
    resp = client.document_text_detection(
        image=image,
        image_context=image_context
    )
    # Extract words with polygons (image pixel coordinates)
    words = []
    for page in resp.full_text_annotation.pages:
        for block in page.blocks:
            for para in block.paragraphs:
                for word in para.words:
                    text = "".join([s.text for s in word.symbols])
                    verts = [(v.x, v.y) for v in word.bounding_box.vertices]
                    words.append({"text": text, "poly": verts})
    return words, resp
```

**Image limits**: keep file size **≤ 20 MB** (image) and request JSON **≤ 10 MB** if you inline base64; prefer URLs/GCS for big files. ([Google Cloud][13])

> **Doubt/Note:** Vision handles many scripts, but vertical Japanese with stylized fonts can still be tricky. Hints help; mixing `TEXT_DETECTION` and `DOCUMENT_TEXT_DETECTION` on edge cases is sometimes useful. I can’t empirically verify your exact manga set here.

### 2.4 KD‑Tree grouping (word → bubble-ish regions)

**Goal:** Merge OCR words into spatial groups that approximate speech bubbles.

**Algorithm (image pixel space):**

1. Convert each word polygon to a **center point** `(cx, cy)` and a **box** `x0,y0,x1,y1`.
2. Estimate a **scale** per image: e.g., median of word heights `Hmed`.
3. Build a **KD‑Tree** over centers. For each point, find neighbors within radius `r ≈ 1.2–1.6 × Hmed`. Add undirected edges. Run **connected components** to form clusters. ([SciPy Documentation][4])
4. For each cluster, compute **union bbox**.
5. Determine **orientation** by regressing the principal axis (PCA) or comparing `Δx/Δy` of word centers—if vertical spread dominates, mark `vertical`, else `horizontal`.

Skeleton:

```python
# server/grouping.py
import numpy as np
from scipy.spatial import KDTree

def to_bbox(poly):
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)

def group_words(words):
    boxes = [to_bbox(w["poly"]) for w in words]
    centers = np.array([((x0+x1)/2, (y0+y1)/2) for (x0,y0,x1,y1) in boxes])
    heights = np.array([y1 - y0 for (x0,y0,x1,y1) in boxes])
    Hmed = np.median(heights) if len(heights) else 20.0
    R = float(Hmed * 1.4)

    tree = KDTree(centers)  # SciPy KDTree for fast neighbor search :contentReference[oaicite:22]{index=22}
    N = len(centers)
    adj = [[] for _ in range(N)]
    for i in range(N):
        # neighbors within radius R
        idxs = tree.query_ball_point(centers[i], r=R)
        for j in idxs:
            if i != j: adj[i].append(j)

    # Connected components
    seen, groups = set(), []
    for i in range(N):
        if i in seen: continue
        stack = [i]; comp = []
        while stack:
            k = stack.pop()
            if k in seen: continue
            seen.add(k); comp.append(k)
            stack.extend(adj[k])
        groups.append(comp)

    out = []
    for gid, comp in enumerate(groups):
        x0 = min(boxes[k][0] for k in comp)
        y0 = min(boxes[k][1] for k in comp)
        x1 = max(boxes[k][2] for k in comp)
        y1 = max(boxes[k][3] for k in comp)
        pts = centers[comp]
        varx, vary = np.var(pts[:,0]), np.var(pts[:,1])
        orient = "vertical" if vary > varx*1.3 else "horizontal"
        out.append({"id": f"g_{gid}", "bbox": (x0,y0,x1,y1), "word_idx": comp, "orientation": orient})
    return out
```

> **Why KD‑Tree?** Sub‑quadratic neighbor discovery; exact `query_ball_point` is standard for radius graphs. Scikit-learn’s `KDTree` offers more metrics if you need them. ([SciPy Documentation][14])

**Reading order per group**

* If `orientation == "vertical"`, sort primarily by **x descending** (right → left), then **y ascending** within column bands; else sort by **y ascending**, then **x ascending** with line batching.
* Join words with spaces/newlines heuristics (small vertical gaps → same line; bigger gaps → new line).

### 2.5 Translation (Cerebras Chat Completions)

Use the **Cerebras Python SDK** and request **structured outputs** so you get reliable, machine‑parseable JSON (no creative flourishes from the model). ([Cerebras Inference][5])

```python
# server/translate.py
import os, json
from cerebras.cloud.sdk import Cerebras

client = Cerebras(api_key=os.environ["CEREBRAS_API_KEY"])

TRANSLATION_SCHEMA = {
  "type": "json_schema",
  "json_schema": {
    "name": "bubble_translations",
    "strict": True,
    "schema": {
      "type": "object",
      "additionalProperties": False,
      "properties": {
        "items": {
          "type": "array",
          "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
              "id": {"type": "string"},
              "en": {"type": "string"}
            },
            "required": ["id","en"]
          }
        }
      },
      "required": ["items"]
    }
  }
}

def translate_groups_jp_to_en(groups):
    # Build a compact prompt: array of {id, jp} to translate
    items = [{"id": g["id"], "jp": g["jp_text"]} for g in groups]
    sys = (
      "You are a professional manga translator. Translate Japanese to natural, "
      "concise English preserving speaker tone. Return JSON only."
    )
    user = "Translate each item.jp to English as item.en (no notes). Items:\n" + json.dumps(items, ensure_ascii=False)
    resp = client.chat.completions.create(
        model="llama-3.3-70b",                       # model list is documented in Cerebras docs
        messages=[{"role": "system", "content": sys},
                  {"role": "user", "content": user}],
        response_format=TRANSLATION_SCHEMA,
        temperature=0.2
    )
    data = json.loads(resp.choices[0].message.content)
    return {row["id"]: row["en"] for row in data["items"]}
```

* Cerebras **Chat Completions** endpoint + Python client usage is documented; **streaming** is available if you later want progressive UI. ([Cerebras Inference][5])

> **Doubt/Note:** LLM translation quality is often excellent but may vary on SFX/onomatopoeia or stylized slang. If you need “MT exactness,” you can keep a toggle to a dedicated MT engine; but since you asked for Cerebras, we stick to it.

### 2.6 End-to-end FastAPI route

```python
# server/main.py
import base64, io, requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from PIL import Image

from ocr import document_ocr
from grouping import group_words
from translate import translate_groups_jp_to_en

app = FastAPI()

class AnalyzeReq(BaseModel):
    image_url: str | None = None
    image_b64: str | None = None
    intrinsic_size: dict | None = None
    language_hint: str | None = "ja"

def _load_image_bytes(req: AnalyzeReq) -> bytes:
    if req.image_b64:
        header, b64 = req.image_b64.split(",", 1) if "," in req.image_b64 else ("", req.image_b64)
        return base64.b64decode(b64)
    if req.image_url:
        # Prefer server-side fetch to avoid the 10MB JSON limit for base64-inlined images. :contentReference[oaicite:26]{index=26}
        r = requests.get(req.image_url, timeout=20)
        r.raise_for_status()
        return r.content
    raise HTTPException(400, "Provide image_url or image_b64")

@app.post("/analyze")
def analyze(req: AnalyzeReq):
    img_bytes = _load_image_bytes(req)
    # (Optional) downscale extremely large bitmaps to keep OCR snappy
    # while respecting Vision limits. :contentReference[oaicite:27]{index=27}
    words, _resp = document_ocr(img_bytes, language_hint=req.language_hint or "ja")
    if not words:
        return {"ocr_image_size": None, "groups": []}

    # Group words
    groups = group_words(words)

    # Build JP text per group in reading order
    for g in groups:
        ws = [words[i] for i in g["word_idx"]]
        # naive reading-order: sort by y, then x; refine per orientation
        if g["orientation"] == "vertical":
            ws.sort(key=lambda w: (sum(p[0] for p in w["poly"])/4, sum(p[1] for p in w["poly"])/4))
            ws.reverse()  # right→left approx
        else:
            ws.sort(key=lambda w: (min(p[1] for p in w["poly"]), min(p[0] for p in w["poly"])))
        g["jp_text"] = " ".join(w["text"] for w in ws)

    # Translate
    trans_map = translate_groups_jp_to_en(groups)
    for g in groups:
        g["en_text"] = trans_map.get(g["id"], "")

    # Report OCR image size using decoded image metadata
    with Image.open(io.BytesIO(img_bytes)) as im:
        w, h = im.size

    # Simplify bbox tuples to dicts
    out = []
    for g in groups:
        x0,y0,x1,y1 = g["bbox"]
        out.append({
          "id": g["id"],
          "bbox": {"x0": int(x0), "y0": int(y0), "x1": int(x1), "y1": int(y1)},
          "orientation": g["orientation"],
          "jp_text": g["jp_text"],
          "en_text": g["en_text"]
        })

    return {"ocr_image_size": {"w": w, "h": h}, "groups": out}

@app.get("/health")
def health():
    return {"status": "ok"}
```

---

## 3) Extension ⇄ API data flow

1. **Content script** enumerates `<img>` elements (heuristic: area > \~160k px).
2. For each image:

   * Compute `rect` (viewport coords) and `naturalWidth/Height` (intrinsic). ([MDN Web Docs][2])
   * Send `{src, intrinsic_size}` to service worker.
3. **Service worker** fetches `src` as bytes (safer for CORS) and `POST /analyze` with `image_url` or forwarded bytes (if you prefer to upload). Cross‑origin allowed via host permissions. ([Chrome for Developers][1])
4. **Server** responds with `ocr_image_size` and grouped translated boxes in **image pixel coordinates**.
5. **Content script** maps each group bbox back to the page:

   ```js
   const sx = rect.width  / ocr_image_size.w;
   const sy = rect.height / ocr_image_size.h;
   const x  = pageX + bbox.x0 * sx;
   const y  = pageY + bbox.y0 * sy;
   const w  = (bbox.x1 - bbox.x0) * sx;
   const h  = (bbox.y1 - bbox.y0) * sy;
   ```
6. Inject overlay DIVs with `en_text`. Maintain their positions with `ResizeObserver` + scroll listeners; throttle for performance. ([MDN Web Docs][8])

---

## 4) Overlay rendering details

* **Container strategy:** create a single absolutely‑positioned container inside `document.body` at `(0,0)` and append child DIVs for each group. Use `position: absolute` and set `left/top/width/height` from the mapped coordinates.
* **Text packing:** set a **max width** (\~90% of group width), `word-break: normal; white-space: pre-wrap;`.
* **Adaptive font size:** if text overflows height, iteratively reduce font-size until it fits or hits a floor.
* **Reactivity:** re‑compute on `resize` and when the `<img>`’s rect changes via `ResizeObserver`. ([MDN Web Docs][8])

---

## 5) Messaging (MV3) snippets

**content.js → service worker**

```js
chrome.runtime.sendMessage({type: "ANALYZE_IMAGE", payload: { src, intrinsicSize }});
```

**service worker**

```js
chrome.runtime.onMessage.addListener(async (msg, sender) => {
  if (msg.type === "ANALYZE_IMAGE") {
    const { src, intrinsicSize } = msg.payload;
    const res = await fetch("https://your-api.example.com/analyze", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ image_url: src, intrinsic_size: intrinsicSize, language_hint:"ja" })
    });
    const data = await res.json();
    if (sender.tab?.id) chrome.tabs.sendMessage(sender.tab.id, { type:"ANALYZE_RESULT", data, src });
  }
});
```

* Pattern follows Chrome’s **message passing** doc; use `runtime.sendMessage` and `tabs.sendMessage`. ([Chrome for Developers][7])

---

## 6) Performance & cost controls

* **Image pre‑checks:** skip tiny images; soft-limit width/height (downscale >3000 px on the server) to keep OCR responsive while respecting Vision limits (≤20 MB file, ≤10 MB JSON if base64). ([Google Cloud][13])
* **Batching:** one page often has multiple images; serialize or small parallelism (2–3 inflight) to avoid throttling.
* **Cache:** hash image bytes → cache OCR+translation for fast revisits.
* **Streaming:** Cerebras supports streaming responses if you want progressive overlays later. ([Cerebras Inference][15])

---

## 7) Security & privacy

* **Never embed keys** in the extension; keep **Google Vision** service account and **Cerebras API key** on the server (env vars, secret manager). ([Google Cloud][12])
* **CORS**: allow your extension’s origin or use a simple bearer token on `POST /analyze`.
* **Host permissions**: scope down in production; for hackathons `<all_urls>` is practical, but least‑privilege is better. ([Chrome for Developers][16])

---

## 8) Edge cases & mitigations

* **Images behind auth / anti-hotlinking:** initial v1 supports publicly accessible images. Fallback: **captureVisibleTab** for raster data at the cost of perfect alignment (you’ll map viewport pixels, not the intrinsic size). ([MDN Web Docs][9])
* **Vertical Japanese**: Vision recognizes Japanese without hints, but **`languageHints: ['ja']`** can help on tricky pages; some glyph variants may still mis-recognize. ([Google Cloud][17])
* **Bounding polygons**: Vision returns 4‑point polygons; we use axis-aligned min/max boxes (simple, robust for overlays). For fancy bubble tracing, add a contour fitting step later. ([Google Cloud][18])
* **Overlays colliding with UI**: use `pointer-events: none` to avoid intercepting clicks.

---

## 9) Test plan

* **Unit**:

  * KD‑Tree grouping forms stable clusters on synthetic grids (golden bbox sets).
  * LLM translation schema is always valid JSON (reject if `response_format` violated). ([Cerebras Inference][5])
* **Integration**:

  * A public manga sample page with 2–3 panels; check alignment on zoom 90–125% and window resize.
  * Throttle network → ensure graceful retries.
* **Manual**:

  * Vertical text sample; capture visible tab fallback on a paywalled page.

---

## 10) “Batteries included” code stubs

### content.js (excerpt)

```js
function findMangaImages() {
  return [...document.images].filter(img => {
    const r = img.getBoundingClientRect();
    return r.width * r.height > 160000; // >400x400
  });
}

async function process() {
  const imgs = findMangaImages();
  for (const img of imgs) {
    const rect = img.getBoundingClientRect();
    const intrinsicSize = {w: img.naturalWidth, h: img.naturalHeight};
    chrome.runtime.sendMessage({
      type: "ANALYZE_IMAGE",
      payload: { src: img.src, intrinsicSize }
    });
  }
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "ANALYZE_RESULT") {
    const { data, src } = msg;
    const img = [...document.images].find(i => i.src === src);
    if (!img) return;
    const rect = img.getBoundingClientRect();
    const pageX = rect.left + window.scrollX;
    const pageY = rect.top  + window.scrollY;

    const sx = rect.width  / data.ocr_image_size.w;
    const sy = rect.height / data.ocr_image_size.h;

    for (const g of data.groups) {
      const x = pageX + g.bbox.x0 * sx;
      const y = pageY + g.bbox.y0 * sy;
      const w = (g.bbox.x1 - g.bbox.x0) * sx;
      const h = (g.bbox.y1 - g.bbox.y0) * sy;
      const el = document.createElement("div");
      el.className = "manga-translate";
      Object.assign(el.style, { left: `${x}px`, top: `${y}px`, width: `${w}px`, minHeight: `${h}px` });
      el.textContent = g.en_text;
      document.body.appendChild(el);
    }
  }
});

process();
```

* The geometry methods are standard and well-defined in MDN. ([MDN Web Docs][2])

---

## 11) Configuration knobs

* **KD radius**: start at `1.4×median(wordHeight)`; adjust per scan.
* **Translation style**: system prompt can enforce brevity or tone (e.g., keep honorifics).
* **Batch size**: 40–60 groups per LLM call is fine for context windows of large models (tune per model limits listed in Cerebras docs). ([Cerebras Inference][5])
* **OCR mode**: try `DOCUMENT_TEXT_DETECTION` first; `TEXT_DETECTION` for sparse pages. ([Google Cloud][19])

---

## 12) Roadmap (post-hackathon)

* **Local caching** of OCR/translation keyed by image hash.
* **Reading-order learning**: lightweight heuristics + vertical/horizontal auto-detection.
* **UI**: toggle original vs. translated, font face for “manga look.”
* **Heavier CV**: optional cloud detector for **bubble contour** (outline-aware overlay) if you want pixel-perfect balloons later.

---

### References (key docs you’ll likely reach for again)

* **Chrome MV3**: host permissions, cross-origin requests; message passing patterns. ([Chrome for Developers][1])
* **Geometry APIs**: `getBoundingClientRect`, `naturalWidth/Height`. ([MDN Web Docs][2])
* **Google Cloud Vision**: OCR overview & `DOCUMENT_TEXT_DETECTION`, bounds sample, full-text tutorial, image/request size limits. ([Google Cloud][19])
* **KD-Tree**: SciPy/Sklearn. ([SciPy Documentation][4])
* **Cerebras**: Chat Completions, QuickStart, streaming. ([Cerebras Inference][5])
* **Viewport capture** (fallback). ([MDN Web Docs][9])

[1]: https://developer.chrome.com/docs/extensions/develop/concepts/network-requests?utm_source=chatgpt.com "Cross-origin network requests | Chrome Extensions"
[2]: https://developer.mozilla.org/en-US/docs/Web/API/Element/getBoundingClientRect?utm_source=chatgpt.com "Element: getBoundingClientRect() method - Web APIs - MDN"
[3]: https://cloud.google.com/vision/docs/fulltext-annotations?utm_source=chatgpt.com "Dense document text detection tutorial | Cloud Vision API"
[4]: https://docs.scipy.org/doc//scipy-1.9.1/reference/generated/scipy.spatial.KDTree.html?utm_source=chatgpt.com "scipy.spatial.KDTree — SciPy v1.9.1 Manual"
[5]: https://inference-docs.cerebras.ai/api-reference/chat-completions "Chat Completions - Cerebras Inference"
[6]: https://developer.chrome.com/docs/extensions/develop/migrate/what-is-mv3?utm_source=chatgpt.com "Extensions / Manifest V3 - Chrome for Developers"
[7]: https://developer.chrome.com/docs/extensions/develop/concepts/messaging?utm_source=chatgpt.com "Message passing | Chrome for Developers"
[8]: https://developer.mozilla.org/en-US/docs/Web/API/ResizeObserver?utm_source=chatgpt.com "ResizeObserver - Web APIs | MDN - Mozilla"
[9]: https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/tabs/captureVisibleTab?utm_source=chatgpt.com "tabs.captureVisibleTab() - Mozilla - MDN"
[10]: https://github.com/googleapis/python-vision?utm_source=chatgpt.com "Python Client for Google Cloud Vision"
[11]: https://pypi.org/project/cerebras-cloud-sdk/?utm_source=chatgpt.com "cerebras-cloud-sdk"
[12]: https://cloud.google.com/docs/authentication/api-keys-best-practices?utm_source=chatgpt.com "Best practices for managing API keys | Authentication"
[13]: https://cloud.google.com/vision/docs/supported-files?utm_source=chatgpt.com "Supported Images | Cloud Vision API"
[14]: https://docs.scipy.org/doc/scipy/reference/generated/scipy.spatial.KDTree.query.html?utm_source=chatgpt.com "query — SciPy v1.16.2 Manual"
[15]: https://inference-docs.cerebras.ai/capabilities/streaming?utm_source=chatgpt.com "Streaming Responses - Build with Cerebras Inference"
[16]: https://developer.chrome.com/docs/extensions/develop/concepts/declare-permissions?utm_source=chatgpt.com "Declare permissions | Chrome Extensions"
[17]: https://cloud.google.com/vision/docs/languages?utm_source=chatgpt.com "OCR Language Support | Cloud Vision API"
[18]: https://cloud.google.com/vision/docs/samples/vision-document-text-tutorial-detect-bounds?utm_source=chatgpt.com "Detect text in a document: Bounds | Cloud Vision API"
[19]: https://cloud.google.com/vision/docs/ocr?utm_source=chatgpt.com "Detect text in images | Cloud Vision API"
