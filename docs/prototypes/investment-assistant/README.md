# Investment Assistant — clickable prototype

Open `index.html` in any browser (no server needed). Rebuild after editing with
`python3 build.py`, which inlines `styles.css`, `data.json`, `app.js` and the
observatory's `tokens.css` into one portable file.

- Screens: Inbox · Desk (org chart + #desk feed) · Specialists (catalogue and
  specialist page) · Research threads · Files · Audit · Settings. Hash routes.
- Working actions: add / update a specialist, approve or decline an outward post,
  @mention a specialist in the feed, ask a specialist in a thread, "Try asking"
  chips, ⌘K search, light/dark, "Reset demo state" in Settings.
- `data.json` is a snapshot of the local Plover Analytics API taken 23 Sep 2026
  (Toyota, MS&AD, SMFG, MUFG, Mizuho, Tokio Marine; CPI July 2026). Replies are
  computed from it in `app.js` — no model is called, every figure is real.
- State lives in localStorage; reset from Settings before a demo.
