const toggleButton = document.getElementById("toggle");
const statusText = document.getElementById("status");
const retranslateLastButton = document.getElementById("retranslate-last");
const retranslateCurrentButton = document.getElementById("retranslate-current");
const retranslateAllButton = document.getElementById("retranslate-all");

let currentEnabled = true;
let pending = false;
let activeTabId = null;
let lastImageAvailable = false;
let currentImageAvailable = false;

function updateRetranslateButtons() {
  const baseDisabled = pending || activeTabId === null;
  retranslateAllButton.disabled = baseDisabled || !currentEnabled;
  retranslateLastButton.disabled = baseDisabled || !currentEnabled || !lastImageAvailable;
  retranslateCurrentButton.disabled = baseDisabled || !currentEnabled || !currentImageAvailable;
}

function updateUi() {
  toggleButton.disabled = pending;
  toggleButton.textContent = currentEnabled ? "Disable translations" : "Enable translations";
  statusText.textContent = currentEnabled
    ? "Translations are currently enabled."
    : "Translations are paused.";
  updateRetranslateButtons();
}

function readState() {
  chrome.storage.sync.get({ translatorEnabled: true }, (result) => {
    currentEnabled = Boolean(result.translatorEnabled);
    pending = false;
    updateUi();
  });
}

function refreshActiveTabState() {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (chrome.runtime.lastError || !tabs.length) {
      activeTabId = null;
      lastImageAvailable = false;
      currentImageAvailable = false;
      updateUi();
      return;
    }
    const tab = tabs[0];
    activeTabId = tab.id ?? null;
    if (activeTabId === null) {
      lastImageAvailable = false;
      currentImageAvailable = false;
      updateUi();
      return;
    }
    chrome.tabs.sendMessage(
      activeTabId,
      { type: "GET_TRANSLATOR_STATUS" },
      (response) => {
        if (!chrome.runtime.lastError && response) {
          if (typeof response.enabled === "boolean") {
            currentEnabled = response.enabled;
          }
          lastImageAvailable = Boolean(response.hasLastImage);
          currentImageAvailable = Boolean(response.currentImageId || response.hasCurrentImage);
        } else {
          lastImageAvailable = false;
          currentImageAvailable = false;
        }
        updateUi();
      }
    );
  });
}

toggleButton.addEventListener("click", () => {
  if (pending) return;
  pending = true;
  currentEnabled = !currentEnabled;
  updateUi();
  chrome.storage.sync.set({ translatorEnabled: currentEnabled }, () => {
    pending = false;
    if (chrome.runtime.lastError) {
      console.error("Failed updating translator state", chrome.runtime.lastError);
    }
    readState();
    chrome.tabs.query({}, (tabs) => {
      for (const tab of tabs) {
        if (tab.id === undefined) continue;
        chrome.tabs.sendMessage(
          tab.id,
          {
            type: "TRANSLATOR_TOGGLE",
            enabled: currentEnabled,
          },
          () => chrome.runtime.lastError
        );
      }
    });
    refreshActiveTabState();
  });
});

retranslateAllButton.addEventListener("click", () => {
  if (activeTabId === null || !currentEnabled) return;
  retranslateAllButton.disabled = true;
  chrome.tabs.sendMessage(
    activeTabId,
    { type: "RETRANSLATE_ALL" },
    () => {
      if (chrome.runtime.lastError) {
        console.warn("Retranslate all failed", chrome.runtime.lastError);
      }
      refreshActiveTabState();
    }
  );
});

retranslateLastButton.addEventListener("click", () => {
  if (activeTabId === null || !currentEnabled || !lastImageAvailable) return;
  retranslateLastButton.disabled = true;
  chrome.tabs.sendMessage(
    activeTabId,
    { type: "RETRANSLATE_LAST" },
    () => {
      if (chrome.runtime.lastError) {
        console.warn("Retranslate last failed", chrome.runtime.lastError);
      }
      refreshActiveTabState();
    }
  );
});

retranslateCurrentButton.addEventListener("click", () => {
  if (activeTabId === null || !currentEnabled || !currentImageAvailable) return;
  retranslateCurrentButton.disabled = true;
  chrome.tabs.sendMessage(
    activeTabId,
    { type: "RETRANSLATE_CURRENT" },
    () => {
      if (chrome.runtime.lastError) {
        console.warn("Retranslate current failed", chrome.runtime.lastError);
      }
      refreshActiveTabState();
    }
  );
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "sync" || !changes.translatorEnabled) return;
  currentEnabled = Boolean(changes.translatorEnabled.newValue);
  updateUi();
  refreshActiveTabState();
});

readState();
refreshActiveTabState();
