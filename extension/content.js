const MIN_IMAGE_AREA = 400 * 400;
const PROCESSED_ATTR = "data-mt-processed";
const STYLE_ID = "manga-translator-style";
const OVERLAY_CLASS = "manga-translate";
const CACHE_STORAGE_KEY = "translationCache";

const IMAGE_STATE = new Map();
let overlayRoot = null;
let globalListenersBound = false;
let extensionEnabled = true;
let translationCache = {};
let cacheLoaded = false;
let bootstrapQueued = false;
let lastInteractedImageId = null;
let currentVisibleImageId = null;
let visibilityObserver = null;
const visibilityRatios = new Map();

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
      display: flex;
      align-items: center;
      justify-content: center;
      text-align: center;
      overflow: hidden;
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
    if (!extensionEnabled) return;
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

function recomputeCurrentVisible() {
  let bestId = null;
  let bestRatio = 0;
  for (const [id, ratio] of visibilityRatios.entries()) {
    if (ratio > bestRatio) {
      bestRatio = ratio;
      bestId = id;
    }
  }
  if (bestRatio < 0.05) {
    bestId = null;
  }
  if (currentVisibleImageId !== bestId) {
    currentVisibleImageId = bestId;
  }
}

function ensureVisibilityObserver() {
  if (visibilityObserver) return visibilityObserver;
  visibilityObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const img = entry.target;
        const id = img?.dataset?.mtId;
        if (!id) continue;
        const ratio = entry.isIntersecting ? entry.intersectionRatio : 0;
        visibilityRatios.set(id, ratio);
      }
      recomputeCurrentVisible();
    },
    {
      threshold: [0, 0.05, 0.1, 0.25, 0.5, 0.75, 1],
    }
  );
  return visibilityObserver;
}

function buildCacheKey(src, size) {
  const width = size?.w ?? size?.width ?? 0;
  const height = size?.h ?? size?.height ?? 0;
  return `${src}__${width}x${height}`;
}

function getCachedEntry(key) {
  if (!key) return null;
  const entry = translationCache[key];
  if (!entry) {
    return null;
  }
  if (entry.data) {
    return entry;
  }
  return { data: entry, timestamp: Date.now() };
}

function storeCacheEntry(key, data) {
  if (!key || !data) return;
  translationCache[key] = { data, timestamp: Date.now() };
  chrome.storage.local.set({ [CACHE_STORAGE_KEY]: translationCache });
}

function deleteCacheEntry(key) {
  if (!key) return;
  if (translationCache[key]) {
    delete translationCache[key];
    chrome.storage.local.set({ [CACHE_STORAGE_KEY]: translationCache });
  }
}

function generateImageId(img) {
  if (!img.dataset.mtId) {
    img.dataset.mtId = `mt-${Math.random().toString(36).slice(2, 9)}`;
  }
  return img.dataset.mtId;
}

function trackImage(img) {
  const id = generateImageId(img);
  let state = IMAGE_STATE.get(id);
  if (!state) {
    state = {
      id,
      img,
      overlays: [],
      latestResult: null,
      resizeObserver: null,
      cacheKey: "",
      cacheKeyFallback: "",
    };
    const observer = new ResizeObserver(() => {
      if (extensionEnabled && state.latestResult) {
        renderOverlays(state);
      }
    });
    observer.observe(img);
    state.resizeObserver = observer;
    IMAGE_STATE.set(id, state);
  }
  const visObserver = ensureVisibilityObserver();
  try {
    visObserver.observe(img);
  } catch (err) {
    console.warn("Failed to observe image visibility", err);
  }
  if (!visibilityRatios.has(id)) {
    visibilityRatios.set(id, 0);
    recomputeCurrentVisible();
  }
  const src = img.currentSrc || img.src || "";
  const width = img.naturalWidth || img.width || 0;
  const height = img.naturalHeight || img.height || 0;
  const primaryKey = buildCacheKey(src, { w: width, h: height }) || src;
  state.cacheKey = primaryKey;
  state.cacheKeyFallback = src;
  if (!img.dataset.mtTracked) {
    const markActive = () => {
      lastInteractedImageId = state.id;
    };
    img.addEventListener("mouseenter", markActive);
    img.addEventListener("focus", markActive, true);
    img.addEventListener("click", markActive);
    img.dataset.mtTracked = "1";
  }
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
  if (visibilityObserver) {
    try {
      visibilityObserver.unobserve(state.img);
    } catch (err) {
      console.warn("Failed to unobserve image visibility", err);
    }
  }
  for (const el of state.overlays) {
    el.remove();
  }
  visibilityRatios.delete(id);
  IMAGE_STATE.delete(id);
  if (currentVisibleImageId === id) {
    recomputeCurrentVisible();
  }
}

function findCandidateImages(includeProcessed = false) {
  return Array.from(document.images).filter((img) => {
    if (!includeProcessed && img.hasAttribute(PROCESSED_ATTR)) return false;
    const rect = img.getBoundingClientRect();
    const area = rect.width * rect.height;
    return area >= MIN_IMAGE_AREA;
  });
}

function applyCachedResult(img, cacheEntry) {
  if (!cacheEntry) return;
  const state = trackImage(img);
  state.latestResult = cacheEntry.data;
  img.setAttribute(PROCESSED_ATTR, "1");
  renderOverlays(state);
}

function requestAnalysis(img, options = {}) {
  const { force = false } = options;
  if (!img) return;
  const state = trackImage(img);
  const cacheKey = state.cacheKey;
  const fallbackKey = state.cacheKeyFallback;

  if (!force) {
    const cached = cacheLoaded
      ? getCachedEntry(cacheKey) || getCachedEntry(fallbackKey)
      : null;
    if (cached) {
      applyCachedResult(img, cached);
      return;
    }
  }

  if (!extensionEnabled && !force) return;

  if (!force && img.hasAttribute(PROCESSED_ATTR)) {
    return;
  }
  if (force) {
    deleteCacheEntry(cacheKey);
    if (fallbackKey && fallbackKey !== cacheKey) {
      deleteCacheEntry(fallbackKey);
    }
    state.latestResult = null;
    if (img.hasAttribute(PROCESSED_ATTR)) {
      img.removeAttribute(PROCESSED_ATTR);
    }
  }

  if (!extensionEnabled) return;

  img.setAttribute(PROCESSED_ATTR, "1");
  const intrinsicSize = {
    w: img.naturalWidth || img.width,
    h: img.naturalHeight || img.height,
  };
  const payload = {
    id: state.id,
    src: img.currentSrc || img.src,
    intrinsicSize,
    referrer: window.location.href,
  };
  chrome.runtime.sendMessage({
    type: "ANALYZE_IMAGE",
    payload,
  });
}

function renderOverlays(state) {
  if (!extensionEnabled) return;
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
    el.style.height = `${height}px`;
    el.textContent = group.en_text || group.kr_text || "";
    adjustFontSize(el);
  }
}

function adjustFontSize(el) {
  const minSize = 8;
  const maxSize = 20;
  el.style.fontSize = `${maxSize}px`;
  el.style.lineHeight = "1.3";

  let currentSize = maxSize;
  while (
    (el.scrollHeight > el.clientHeight || el.scrollWidth > el.clientWidth) &&
    currentSize > minSize
  ) {
    currentSize -= 1;
    el.style.fontSize = `${currentSize}px`;
  }
}

function bootstrap() {
  if (!extensionEnabled) return;
  if (!cacheLoaded) {
    bootstrapQueued = true;
    return;
  }
  bootstrapQueued = false;
  bindGlobalListeners();
  ensureStyleElement();
  ensureOverlayRoot();
  const candidates = findCandidateImages();
  for (const img of candidates) {
    requestAnalysis(img);
  }
}

function clearAllOverlays() {
  const entries = Array.from(IMAGE_STATE.keys());
  for (const id of entries) {
    cleanupState(id);
  }
  document.querySelectorAll(`img[${PROCESSED_ATTR}]`).forEach((img) => {
    img.removeAttribute(PROCESSED_ATTR);
  });
  visibilityRatios.clear();
  currentVisibleImageId = null;
}

function setExtensionEnabled(enabled) {
  if (extensionEnabled === enabled) return;
  extensionEnabled = enabled;
  if (!extensionEnabled) {
    clearAllOverlays();
    lastInteractedImageId = null;
  } else {
    bootstrap();
  }
}

function forceRetranslateImageById(id) {
  if (!id) return false;
  if (!extensionEnabled) return false;
  const state = IMAGE_STATE.get(id);
  const img = state?.img || Array.from(document.images).find((node) => node.dataset.mtId === id);
  if (!img) return false;
  requestAnalysis(img, { force: true });
  return true;
}

function forceRetranslateAll() {
  if (!extensionEnabled) return;
  const candidates = findCandidateImages(true);
  for (const img of candidates) {
    requestAnalysis(img, { force: true });
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message) return;
  if (message.type === "ANALYZE_RESULT") {
    const { data, id, src } = message;
    if (!id || !data) return;
    let state = IMAGE_STATE.get(id);
    if (!state && extensionEnabled) {
      const imgNode = Array.from(document.images).find((node) => node.dataset.mtId === id);
      if (imgNode) {
        state = trackImage(imgNode);
      }
    }
    if (state) {
      state.latestResult = data;
      if (extensionEnabled) {
        state.img.setAttribute(PROCESSED_ATTR, "1");
        renderOverlays(state);
      }
    }
    const primaryKey = state?.cacheKey || buildCacheKey(src || "", data?.ocr_image_size || null);
    const fallbackKey = state?.cacheKeyFallback || src || "";
    if (primaryKey) {
      storeCacheEntry(primaryKey, data);
    }
    if (fallbackKey && fallbackKey !== primaryKey) {
      storeCacheEntry(fallbackKey, data);
    }
    return;
  }
  if (message.type === "ANALYZE_ERROR") {
    console.warn("Manga translator error:", message.error);
    return;
  }
  if (message.type === "TRANSLATOR_TOGGLE") {
    setExtensionEnabled(Boolean(message.enabled));
    return;
  }
  if (message.type === "GET_TRANSLATOR_STATUS") {
    sendResponse?.({
      enabled: extensionEnabled,
      hasLastImage: Boolean(lastInteractedImageId),
      hasCurrentImage: Boolean(currentVisibleImageId),
      currentImageId: currentVisibleImageId,
    });
    return;
  }
  if (message.type === "RETRANSLATE_ALL") {
    forceRetranslateAll();
    sendResponse?.({ ok: true });
    return;
  }
  if (message.type === "RETRANSLATE_CURRENT") {
    const targetId = message.imageId || currentVisibleImageId;
    const ok = forceRetranslateImageById(targetId);
    sendResponse?.({ ok, imageId: targetId });
    return;
  }
  if (message.type === "RETRANSLATE_LAST") {
    const targetId = message.imageId || lastInteractedImageId;
    const ok = forceRetranslateImageById(targetId);
    sendResponse?.({ ok, imageId: targetId });
  }
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "sync" && changes.translatorEnabled) {
    setExtensionEnabled(Boolean(changes.translatorEnabled.newValue));
  }
  if (area === "local" && changes[CACHE_STORAGE_KEY]) {
    translationCache = changes[CACHE_STORAGE_KEY].newValue || {};
  }
});

const observer = new MutationObserver((mutations) => {
  for (const mutation of mutations) {
    mutation.addedNodes.forEach((node) => {
      if (!extensionEnabled) return;
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

chrome.storage.sync.get({ translatorEnabled: true }, (result) => {
  extensionEnabled = Boolean(result.translatorEnabled);
  chrome.storage.local.get({ [CACHE_STORAGE_KEY]: {} }, (cacheResult) => {
    const stored = cacheResult[CACHE_STORAGE_KEY] || {};
    translationCache = { ...stored, ...translationCache };
    cacheLoaded = true;
    if (extensionEnabled && (bootstrapQueued || document.readyState !== "loading")) {
      bootstrap();
    }
  });
});

document.addEventListener(
  "DOMContentLoaded",
  () => {
    bootstrap();
  },
  { once: true }
);
if (document.readyState === "interactive" || document.readyState === "complete") {
  bootstrap();
}
