const API_BASE_URL = "http://localhost:8000";

function getTranslatorEnabled() {
  return new Promise((resolve) => {
    chrome.storage.sync.get({ translatorEnabled: true }, (result) => {
      resolve(Boolean(result.translatorEnabled));
    });
  });
}

async function fetchImageAsBase64(url, referrer) {
  const headers = new Headers();
  // Add the Referer header if it was provided
  if (referrer) {
    headers.append('Referer', referrer);
  }

  const response = await fetch(url, {
    credentials: 'include',
    headers: headers, // Use the new headers
  });

  if (!response.ok) {
    // Improved error message with status code
    throw new Error(`Failed to fetch image: ${response.status} ${response.statusText}`);
  }

  const buffer = await response.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.byteLength; i += 1) {
    binary += String.fromCharCode(bytes[i]);
  }
  const b64 = btoa(binary);
  const contentType = response.headers.get('content-type') || 'image/jpeg';
  return `data:${contentType};base64,${b64}`;
}

async function callAnalyzeApi(payload, imageB64) {
  const body = {
    image_b64: imageB64,
    intrinsic_size: payload.intrinsicSize,
    language_hint: "ko",
    context_id: payload.referrer || payload.src || null,
  };
  const res = await fetch(`${API_BASE_URL}/analyze`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API error ${res.status}: ${text}`);
  }
  return res.json();
}

async function analyzeImage(payload, sender) {
  try {
    const enabled = await getTranslatorEnabled();
    if (!enabled) {
      return;
    }
    const imageB64 = await fetchImageAsBase64(payload.src, payload.referrer);
    const data = await callAnalyzeApi(payload, imageB64);
    if (sender.tab?.id !== undefined) {
      chrome.tabs.sendMessage(sender.tab.id, {
        type: "ANALYZE_RESULT",
        id: payload.id,
        data,
        src: payload.src,
      });
    }
  } catch (error) {
    console.error("Manga Translator: Analysis failed!", {
      error: error,
      payload: payload,
    });
    console.error("Analysis failed", error);
    if (sender.tab?.id !== undefined) {
      chrome.tabs.sendMessage(sender.tab.id, {
        type: "ANALYZE_ERROR",
        id: payload.id,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }
}

chrome.runtime.onMessage.addListener((message, sender) => {
  if (message?.type === "ANALYZE_IMAGE") {
    analyzeImage(message.payload, sender);
  }
});
