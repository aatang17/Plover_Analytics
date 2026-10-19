/* Save to Plover: one click on the toolbar button.

   The article is read in the reader's own tab, where they are signed in to
   the publisher (ploverExtract, extract.js: the same function Plover's
   bookmark uses). The extension then opens Plover's save page with the text
   in the address's #fragment, which no request carries to any server; that
   page saves it with the reader's Plover session. The extension holds no
   password, key or cookie of its own, and reads a page only when clicked
   (the activeTab permission). */
importScripts("config.js", "extract.js");

function openSave(page) {
  chrome.tabs.create({ url: PLOVER_ORIGIN + "/save.html#" + encodeURIComponent(JSON.stringify(page)) });
}

chrome.action.onClicked.addListener(function (tab) {
  if (!tab || !tab.id || !/^https?:/i.test(tab.url || "")) {
    openSave({ error: "page" });
    return;
  }
  chrome.scripting.executeScript({ target: { tabId: tab.id }, func: ploverExtract })
    .then(function (results) {
      var page = results && results[0] && results[0].result;
      openSave(page && page.url ? page : { error: "page" });
    })
    .catch(function () { openSave({ error: "page" }); });
});
