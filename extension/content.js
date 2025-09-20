const MIN_IMAGE_AREA = 400 * 400;
const PROCESSED_ATTR = "data-mt-processed";
const STYLE_ID = "manga-translator-style";
const OVERLAY_CLASS = "manga-translate";
const IMAGE_STATE = new Map();
let overlayRoot = null;
let globalListenersBound = false;

function ensureStyleElement() {
  if (document.getElementById(STYLE_ID)) {
    return;
  }
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
    .${OVERLAY_CLASS} {
      position: absolute;
      pointer-events: none;
      background: rgba(255, 255, 255, 0.88);
      border-radius: 10px;
      padding: 6px 8px;
      font: 600 14px/1.35 system-ui, -apple-system, "Segoe UI", Arial, sans-serif;
      color: #111;
      text-shadow: 0 1px 0 rgba(255, 255, 255, 0.4);
      box-shadow: 0 6px 18px rgba(15, 23, 42, 0.18);
      white-space: pre-wrap;
      word-break: break-word;
      mix-blend-mode: lighten;
    }
  `;
  document.head.appendChild(style);
}

function ensureOverlayRoot() {
  if (overlayRoot && overlayRoot.isConnected) {
    return overlayRoot;
  }
  overlayRoot = document.createElement("div");
  overlayRoot.style.position = "absolute";
  overlayRoot.style.left = "0";
  overlayRoot.style.top = "0";
  overlayRoot.style.width = "0";
  overlayRoot.style.height = "0";
  overlayRoot.style.zIndex = "2147483647";
  overlayRoot.style.pointerEvents = "none";
  document.body.appendChild(overlayRoot);
  return overlayRoot;
}

function bindGlobalListeners() {
  if (globalListenersBound) return;
  const handle = () => {
    for (const state of IMAGE_STATE.values()) {
      if (state.latestResult) {
        renderOverlays(state);
      }
    }
  };
  window.addEventListener("scroll", handle, { passive: true });
  window.addEventListener("resize", handle);
  globalListenersBound = true;
}

function generateImageId(img) {
  if (!img.dataset.mtId) {
    img.dataset.mtId = `mt-${Math.random().toString(36).slice(2, 9)}`;
  }
  return img.dataset.mtId;
}

function trackImage(img) {
  const id = generateImageId(img);
  if (IMAGE_STATE.has(id)) {
    return IMAGE_STATE.get(id);
  }
  const state = {
    id,
    img,
    overlays: [],
    latestResult: null,
    resizeObserver: null,
  };
  const observer = new ResizeObserver(() => {
    if (state.latestResult) {
      renderOverlays(state);
    }
  });
  observer.observe(img);
  state.resizeObserver = observer;
  IMAGE_STATE.set(id, state);
  return state;
}

function cleanupState(id) {
  const state = IMAGE_STATE.get(id);
  if (!state) return;
  if (state.resizeObserver) {
    try {
      state.resizeObserver.disconnect();
    } catch (err) {
      console.warn("Failed to disconnect ResizeObserver", err);
    }
  }
  for (const el of state.overlays) {
    el.remove();
  }
  IMAGE_STATE.delete(id);
}

function findCandidateImages() {
  return Array.from(document.images).filter((img) => {
    if (img.hasAttribute(PROCESSED_ATTR)) return false;
    const rect = img.getBoundingClientRect();
    const area = rect.width * rect.height;
    return area >= MIN_IMAGE_AREA;
  });
}

function requestAnalysis(img) {
  if (!img || img.hasAttribute(PROCESSED_ATTR)) return;
  img.setAttribute(PROCESSED_ATTR, "1");
  const intrinsicSize = {
    w: img.naturalWidth || img.width,
    h: img.naturalHeight || img.height,
  };
  const id = generateImageId(img);
  trackImage(img);
  chrome.runtime.sendMessage({
    type: "ANALYZE_IMAGE",
    payload: {
      id,
      src: img.currentSrc || img.src,
      intrinsicSize,
    },
  });
}

function renderOverlays(state) {
  ensureStyleElement();
  const root = ensureOverlayRoot();
  const { img, latestResult } = state;
  if (!img.isConnected) {
    cleanupState(state.id);
    return;
  }
  const rect = img.getBoundingClientRect();
  const pageX = rect.left + window.scrollX;
  const pageY = rect.top + window.scrollY;
  const ocrSize = latestResult?.ocr_image_size;
  if (!ocrSize || !ocrSize.w || !ocrSize.h) {
    return;
  }
  const scaleX = rect.width / ocrSize.w;
  const scaleY = rect.height / ocrSize.h;

  // Ensure overlay count matches groups
  const groups = latestResult.groups || [];
  while (state.overlays.length > groups.length) {
    const el = state.overlays.pop();
    if (el) el.remove();
  }
  for (let i = 0; i < groups.length; i += 1) {
    const group = groups[i];
    let el = state.overlays[i];
    if (!el) {
      el = document.createElement("div");
      el.className = OVERLAY_CLASS;
      root.appendChild(el);
      state.overlays[i] = el;
    }
    const width = (group.bbox.x1 - group.bbox.x0) * scaleX;
    const height = (group.bbox.y1 - group.bbox.y0) * scaleY;
    const left = pageX + group.bbox.x0 * scaleX;
    const top = pageY + group.bbox.y0 * scaleY;
    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
    el.style.width = `${width}px`;
    el.style.minHeight = `${height}px`;
    el.textContent = group.en_text || group.jp_text || "";
    adjustFontSize(el, height);
  }
}

function adjustFontSize(el, targetHeight) {
  if (!targetHeight || targetHeight <= 0) return;
  let size = 18;
  const minSize = 10;
  el.style.fontSize = `${size}px`;
  el.style.lineHeight = "1.35";
  for (let i = 0; i < 5; i += 1) {
    if (el.scrollHeight <= targetHeight || size <= minSize) {
      break;
    }
    size = Math.max(minSize, size - 2);
    el.style.fontSize = `${size}px`;
  }
}

function bootstrap() {
  bindGlobalListeners();
  ensureStyleElement();
  ensureOverlayRoot();
  const candidates = findCandidateImages();
  for (const img of candidates) {
    requestAnalysis(img);
  }
}

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === "ANALYZE_RESULT") {
    const { data, id } = message;
    if (!id) return;
    const state = IMAGE_STATE.get(id);
    if (!state) return;
    state.latestResult = data;
    renderOverlays(state);
  }
  if (message?.type === "ANALYZE_ERROR") {
    console.warn("Manga translator error:", message.error);
  }
});

const observer = new MutationObserver((mutations) => {
  for (const mutation of mutations) {
    mutation.addedNodes.forEach((node) => {
      if (node instanceof HTMLImageElement) {
        if (node.getBoundingClientRect().width * node.getBoundingClientRect().height >= MIN_IMAGE_AREA) {
          requestAnalysis(node);
        }
      } else if (node instanceof HTMLElement) {
        node.querySelectorAll?.("img").forEach((img) => {
          if (img.getBoundingClientRect().width * img.getBoundingClientRect().height >= MIN_IMAGE_AREA) {
            requestAnalysis(img);
          }
        });
      }
    });
  }
});

observer.observe(document.documentElement || document.body, {
  childList: true,
  subtree: true,
});

document.addEventListener("DOMContentLoaded", bootstrap, { once: true });
if (document.readyState === "interactive" || document.readyState === "complete") {
  bootstrap();
}
