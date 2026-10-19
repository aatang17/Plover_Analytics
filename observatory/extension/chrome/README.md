# Save to Plover — Chrome extension

Saves the article in the current tab to the reader's Plover account (Your
Plover › Your Reading), where it can be summarised.

- `background.js` — on a click of the toolbar button, reads the article with
  `ploverExtract` and opens `<server>/save.html#<article>`.
- `extract.js` — not kept here: the download adds `web/assets/plover-extract.js`
  under this name, so the extension and the bookmark read pages the same way.
- `config.js` — the server; the download fills it in for the server it came from.

Readers get it from `/api/v1/assistant/reading/extension.zip` (a button on
my.html), built from this folder on each request (app/assistant/reading.py).
To try a change: download the zip from a local server, unzip, then
chrome://extensions › Developer mode › Load unpacked.

Not done yet: a Chrome Web Store listing (needs the owner's developer account),
icons, and other browsers.
