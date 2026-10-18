/*
 * mdclip service worker.
 *
 * Its only job is to inject the converter into the tab you are looking at,
 * when you ask for it.
 *
 * WHY INJECT ON DEMAND instead of declaring content_scripts in the manifest:
 * a declared content script needs host permissions ("read your data on all
 * websites"), granted permanently at install time. Injecting via activeTab
 * means the extension can only touch a page in the moment you click the button
 * or press the shortcut, and has no access at any other time. That is the
 * whole security argument for building this rather than installing one.
 */

async function copyAsMarkdown(tab, mode) {
  mode = mode || "page";
  if (!tab || !tab.id) return;

  // Chrome/Brave refuse injection into their own pages; say so rather than
  // failing silently, because the button looks broken otherwise.
  const url = tab.url || "";
  if (/^(chrome|brave|edge|about|chrome-extension|devtools):/i.test(url)) {
    await chrome.action.setBadgeText({ text: "n/a", tabId: tab.id });
    setTimeout(() => chrome.action.setBadgeText({ text: "", tabId: tab.id }), 2000);
    return;
  }

  try {
    /*
     * The mode is handed over by setting a global first, because executeScript
     * cannot pass arguments alongside `files`. Doing it this way avoids the
     * "storage" permission a remembered toggle would need -- the permission
     * list is the security claim of this extension, so it stays at two.
     */
    await chrome.scripting.executeScript({
      target: { tabId: tab.id, allFrames: false },
      func: (m) => { window.__MDCLIP_MODE = m; },
      args: [mode],
    });

    // Order matters: converter.js defines window.MDCLIP, content.js calls it.
    await chrome.scripting.executeScript({
      target: { tabId: tab.id, allFrames: false },
      files: ["converter.js", "content.js"],
    });
  } catch (err) {
    // The usual cause is a page the extension has no access to (the Chrome Web
    // Store, a PDF viewer, a file:// page without the file-access toggle).
    await chrome.action.setBadgeText({ text: "err", tabId: tab.id });
    await chrome.action.setTitle({
      title: "mdclip could not read this page: " + (err && err.message ? err.message : err),
      tabId: tab.id,
    });
    setTimeout(() => chrome.action.setBadgeText({ text: "", tabId: tab.id }), 3000);
  }
}

// Clicking the icon gives the whole page; reader mode is the keyboard command.
chrome.action.onClicked.addListener((tab) => copyAsMarkdown(tab, "page"));

chrome.commands.onCommand.addListener(async (command) => {
  const mode = command === "copy-article-markdown" ? "article"
             : command === "copy-as-markdown" ? "page"
             : null;
  if (!mode) return;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  copyAsMarkdown(tab, mode);
});
