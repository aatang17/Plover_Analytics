// Use every search box like a reader would, in a hidden Chrome.
//
//   node ci/search_examples.mjs <base-url> [part,part,...]
//
// The optional parts pick boxes whose key contains any of them ("agm,risks");
// "lock" picks the one-click-one-request check. With none, everything runs.
//
// For each box in ci/search_boxes.json: open its page, type each example the
// box itself shows (and its extra queries) with real key input, wait for the
// page to settle, and require at least one result. Exit 1 if any example finds
// nothing. Run through ci/search_examples.py, which starts a server first.
// TIMING=1 prints each search and how long it took.
//
// One Chrome, three tabs (the Mac's three-at-once rule). Chrome runs with its
// own profile folder and is stopped by its own process id only, never by
// name: a name match would close the person's real browser.
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE = (process.argv[2] || "http://127.0.0.1:8007").replace(/\/$/, "");
const ONLY = (process.argv[3] || "").split(",").map((s) => s.trim()).filter(Boolean);
const TABS = 3;
const CHROME = process.env.CHROME_BIN ||
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const MANIFEST = JSON.parse(readFileSync(join(HERE, "search_boxes.json"), "utf8"));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function examplesOf(box, placeholder) {
  const out = [];
  if (box.placeholder_examples !== false && placeholder) {
    const m = placeholder.match(/\be\.g\.?\s*(.*)$/i);
    const list = m ? m[1] : placeholder;
    for (const part of list.split(/[,、—–]/)) {
      const ex = part.replace(/[…\.]+$/, "").trim();
      if (ex) out.push(ex);
    }
  }
  for (const ex of box.extra || []) if (!out.includes(ex)) out.push(ex);
  return out;
}

async function startChrome() {
  if (!existsSync(CHROME)) throw new Error("Chrome not found at " + CHROME + " (set CHROME_BIN)");
  const profile = mkdtempSync(join(tmpdir(), "plover-search-"));
  const proc = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--user-data-dir=" + profile, "--remote-debugging-port=0", "--window-size=1440,1000",
    "about:blank",
  ], { stdio: "ignore" });
  // Stop our Chrome by its own process id, wait for it to exit, then remove
  // its profile folder (Chrome writes to it until the moment it is gone).
  const stop = async () => {
    const gone = new Promise((r) => { proc.once("exit", r); setTimeout(r, 5000); });
    try { proc.kill("SIGTERM"); } catch (e) { /* already gone */ }
    await gone;
    try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* best effort */ }
  };
  const portFile = join(profile, "DevToolsActivePort");
  for (let i = 0; i < 100 && !existsSync(portFile); i++) await sleep(100);
  if (!existsSync(portFile)) { await stop(); throw new Error("Chrome did not start"); }
  await sleep(100);
  const port = readFileSync(portFile, "utf8").split("\n")[0].trim();
  const newTab = async () => {
    const t = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json();
    return t.webSocketDebuggerUrl;
  };
  return { newTab, stop };
}

function connect(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    let id = 0;
    const waiting = new Map();
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && waiting.has(msg.id)) {
        const { ok, fail } = waiting.get(msg.id);
        waiting.delete(msg.id);
        msg.error ? fail(new Error(msg.error.message)) : ok(msg.result);
      }
    };
    ws.onerror = reject;
    ws.onopen = () => resolve({
      send(method, params = {}) {
        return new Promise((ok, fail) => {
          id += 1;
          waiting.set(id, { ok, fail });
          ws.send(JSON.stringify({ id, method, params }));
        });
      },
      close() { ws.close(); },
    });
  });
}

async function waitFor(fn, ms) {
  const until = Date.now() + ms;
  while (Date.now() < until) { if (await fn()) return true; await sleep(150); }
  return false;
}

// One tab working through its share of the boxes.
async function worker(wsUrl, boxes, report) {
  const cdp = await connect(wsUrl);
  const evaluate = async (expr) =>
    (await cdp.send("Runtime.evaluate", { expression: expr, returnByValue: true })).result.value;
  const q = (sel) => JSON.stringify(sel);
  const count = (sel) => evaluate(`Array.prototype.filter.call(document.querySelectorAll(${q(sel)}),
    e => e.getClientRects().length > 0).length`);
  const areaHtml = (sel) => evaluate(`(document.querySelector(${q(sel)}) || {}).innerHTML || ""`);
  const key = (k, code, vk) => Promise.all(["keyDown", "keyUp"].map((type) =>
    cdp.send("Input.dispatchKeyEvent", { type, key: k, code, windowsVirtualKeyCode: vk })));

  // Wait until the result area has changed from `before` and stopped changing.
  async function settle(area, before) {
    await sleep(300);
    const until = Date.now() + 8000;
    let last = await areaHtml(area);
    let stableSince = Date.now();
    while (Date.now() < until) {
      await sleep(120);
      const now = await areaHtml(area);
      if (now !== last) { last = now; stableSince = Date.now(); continue; }
      if (now !== before && Date.now() - stableSince >= 450) return;
    }
  }

  await cdp.send("Page.enable");
  try {
    for (const box of boxes) {
      await cdp.send("Page.navigate", { url: BASE + box.url });
      const loaded = await waitFor(async () =>
        (await evaluate(`document.readyState === "complete" && !!document.querySelector(${q(box.input)})`)), 20000);
      if (!loaded) { report.failures.push(`${box.key}: the page or its search box did not load`); continue; }
      if (box.ready && !(await waitFor(async () => (await count(box.ready)) > 0, 15000))) {
        report.skipped.push(`${box.key}: the page shows no data on this server`);
        continue;
      }
      await sleep(300);  // a page may set its placeholder, or hide the box, once its data arrives
      if (!(await count(box.input))) {
        report.skipped.push(`${box.key}: the box is hidden on this page; readers use the table's own filter`);
        continue;
      }
      const placeholder = await evaluate(`document.querySelector(${q(box.input)}).placeholder`);
      const examples = examplesOf(box, placeholder);
      if (!examples.length) { report.failures.push(`${box.key}: no examples to try — give it 'extra' queries`); continue; }
      for (const ex of examples) {
        report.tried += 1;
        const t0 = Date.now();
        // clear the box with real keys, as a reader would, then type the example
        await evaluate(`(function(){var el=document.querySelector(${q(box.input)}); el.focus(); el.select(); return true})()`);
        await key("Backspace", "Backspace", 8);
        await sleep(250);
        const before = await areaHtml(box.area);
        await cdp.send("Input.insertText", { text: ex });
        // a box marked "submit" answers on Enter or its button, not as the reader types
        if (box.submit) {
          await sleep(150);
          // Enter carries a carriage return, or Chrome does not submit the form
          await cdp.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13,
                                                     text: "\r", unmodifiedText: "\r" });
          await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
        }
        await settle(box.area, before);
        const n = await count(box.hits);
        if (process.env.TIMING) console.log(`${box.key} "${ex}" ${n} hits ${Date.now() - t0}ms`);
        if (n === 0) report.failures.push(`${box.key}: "${ex}" finds nothing (placeholder: "${placeholder}")`);
      }
    }
  } finally {
    cdp.close();
  }
}

// One click, one request (web/assets/lock.js), checked on a real page: ten
// clicks on a button that sends a change must send it once, and the button
// must work again once the answer is in.
async function lockCheck(wsUrl, report) {
  const cdp = await connect(wsUrl);
  const sent = [];
  const evaluate = async (expr) =>
    (await cdp.send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result.value;
  try {
    await cdp.send("Page.enable");
    await cdp.send("Network.enable");
    const ws = new WebSocket(wsUrl);  // a second listener just for the request log
    await new Promise((ok) => { ws.onopen = ok; });
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.method === "Network.requestWillBeSent" && m.params.request.url.includes("/__lock_probe")) sent.push(m.params.request.method);
    };
    ws.send(JSON.stringify({ id: 1, method: "Network.enable" }));
    await cdp.send("Page.navigate", { url: BASE + "/methodology.html" });
    await waitFor(async () => (await evaluate("document.readyState")) === "complete", 20000);
    const result = await evaluate(`(async function () {
      if (!window.fetch.ploverLocked) return "lock.js is not on the page";
      var b = document.createElement("button");
      b.textContent = "probe";
      document.body.appendChild(b);
      var done;
      b.addEventListener("click", function () {
        done = fetch("/__lock_probe", { method: "POST", body: "{}" }).catch(function () {});
      });
      for (var i = 0; i < 10; i++) b.click();
      if (!b.disabled) return "the button stayed clickable while its request was out";
      await done;
      await new Promise(function (r) { setTimeout(r, 50); });
      if (b.disabled) return "the button stayed off after the answer came back";
      // the same change twice in one go (Enter pressed twice) goes once
      await Promise.all([fetch("/__lock_probe", { method: "PUT", body: "same" }),
                         fetch("/__lock_probe", { method: "PUT", body: "same" })]);
      return "ok";
    })()`);
    await sleep(300);
    ws.close();
    const posts = sent.filter((m) => m === "POST").length;
    const puts = sent.filter((m) => m === "PUT").length;
    if (result !== "ok") report.failures.push("lock.js: " + result);
    else if (posts !== 1) report.failures.push(`lock.js: ten clicks sent ${posts} requests, not 1`);
    else if (puts !== 1) report.failures.push(`lock.js: the same change sent twice at once went ${puts} times, not once`);
    else console.log("ok       lock.js: ten clicks sent one request; the button came back after the answer");
  } finally {
    cdp.close();
  }
}

async function run() {
  const boxes = MANIFEST.boxes.filter((b) => !ONLY.length || ONLY.some((o) => b.key.includes(o)));
  const report = { failures: [], skipped: [], tried: 0 };
  const chrome = await startChrome();
  try {
    if (!ONLY.length || ONLY.includes("lock")) await lockCheck(await chrome.newTab(), report);
    const shares = Array.from({ length: Math.min(TABS, boxes.length) }, () => []);
    boxes.forEach((b, i) => shares[i % shares.length].push(b));
    const tabs = [];
    for (const share of shares) tabs.push(worker(await chrome.newTab(), share, report));
    await Promise.all(tabs);
  } finally {
    await chrome.stop();
  }
  for (const s of report.skipped) console.log("skipped  " + s);
  for (const f of report.failures) console.log("FAIL     " + f);
  console.log(`${report.tried} searches tried across ${boxes.length} boxes; ` +
    `${report.failures.length} failed, ${report.skipped.length} boxes skipped.`);
  return report.failures.length ? 1 : 0;
}

run().then((code) => process.exit(code), (err) => { console.error(err.stack || String(err)); process.exit(2); });
