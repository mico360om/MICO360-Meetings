const statusEl = document.getElementById("status");

document.getElementById("copySelection").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => window.getSelection().toString()
  });

  const text = (result || "").trim();
  if (!text) {
    statusEl.textContent = "No selected text found.";
    return;
  }

  await navigator.clipboard.writeText(`MICO360 Meeting Transcript\n\n${text}`);
  statusEl.textContent = "Copied locally to clipboard.";
});
