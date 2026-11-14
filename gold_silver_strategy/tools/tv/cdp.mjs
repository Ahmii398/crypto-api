// Minimal Chrome DevTools Protocol client for the TradingView tab of the already-running,
// already-logged-in Chrome (started with --remote-debugging-port). Node 22+, no dependencies.
//
// It only looks at and clicks in the page, like a person would. It never reads cookies,
// storage or credentials, and never signs in, signs out or opens an upgrade flow.
//
//   node cdp.mjs shot out.png            screenshot of the tab
//   node cdp.mjs eval "document.title"   evaluate an expression in the page
//   node cdp.mjs run flow.mjs [args]     run `export default async (tv, args) => {...}`

import { writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const PORT = process.env.CDP_PORT || 9399;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function connect(match = "tradingview.com") {
  const targets = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
  const target = targets.find((t) => t.type === "page" && t.url.includes(match));
  if (!target) throw new Error(`no open tab matches ${match}`);
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((ok, fail) => { ws.onopen = ok; ws.onerror = fail; });
  let next = 0;
  const pending = new Map();
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const { ok, fail } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? fail(new Error(msg.error.message)) : ok(msg.result);
    }
  };
  const send = (method, params = {}) => new Promise((ok, fail) => {
    const id = ++next;
    pending.set(id, { ok, fail });
    ws.send(JSON.stringify({ id, method, params }));
  });

  const tv = {
    send, sleep,
    close: () => ws.close(),
    async eval(expression) {
      const { result, exceptionDetails } = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      if (exceptionDetails) throw new Error(exceptionDetails.exception?.description || exceptionDetails.text);
      return result.value;
    },
    // clip = {x, y, width, height} in page pixels; omit for the whole tab
    async shot(file, clip = null) {
      const format = /\.jpe?g$/i.test(file) ? { format: "jpeg", quality: 80 } : { format: "png" };
      const { data } = await send("Page.captureScreenshot", { ...format, ...(clip ? { clip: { ...clip, scale: 1 } } : {}) });
      writeFileSync(file, Buffer.from(data, "base64"));
      return file;
    },
    async navigate(url) {
      await send("Page.navigate", { url });
    },
    async mouse(type, x, y, extra = {}) {
      await send("Input.dispatchMouseEvent", { type, x, y, button: "left", clickCount: 1, ...extra });
    },
    async move(x, y) {
      await send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
    },
    async click(x, y, clickCount = 1) {
      await tv.move(x, y);
      await sleep(60);
      for (let i = 1; i <= clickCount; i++) {
        await tv.mouse("mousePressed", x, y, { clickCount: i });
        await tv.mouse("mouseReleased", x, y, { clickCount: i });
      }
    },
    // key("Enter"), key("a", {ctrl: true}), key("v", {ctrl: true, commands: ["paste"]})
    async key(key, { ctrl = false, shift = false, alt = false, commands = [] } = {}) {
      const named = { Enter: 13, Escape: 27, Tab: 9, Backspace: 8, Delete: 46, ArrowDown: 40, ArrowUp: 38, ArrowLeft: 37, ArrowRight: 39, Home: 36, End: 35 };
      const code = named[key] ?? key.toUpperCase().charCodeAt(0);
      const modifiers = (alt ? 1 : 0) | (ctrl ? 2 : 0) | (shift ? 8 : 0);
      const base = { key, code: named[key] ? key : `Key${key.toUpperCase()}`, windowsVirtualKeyCode: code, nativeVirtualKeyCode: code, modifiers, commands };
      const text = !ctrl && !alt && key.length === 1 ? key : key === "Enter" ? "\r" : undefined;
      await send("Input.dispatchKeyEvent", { type: text ? "keyDown" : "rawKeyDown", ...base, text });
      await send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
    },
    async type(text) {
      await send("Input.insertText", { text });
    },
    // Centre of the first visible element matching `selector` whose text matches `text` (string or regex source).
    async find(selector, text = null, index = 0) {
      const js = `(() => {
        const re = ${text === null ? "null" : `new RegExp(${JSON.stringify(text)})`};
        const hits = [...document.querySelectorAll(${JSON.stringify(selector)})].filter((el) => {
          const r = el.getBoundingClientRect();
          return r.width > 0 && r.height > 0 && (!re || re.test((el.innerText || el.textContent || "").trim()));
        });
        const el = hits[${index}];
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height, n: hits.length, text: (el.innerText || el.textContent || "").trim().slice(0, 120) };
      })()`;
      return tv.eval(js);
    },
    async clickOn(selector, text = null, index = 0) {
      const hit = await tv.find(selector, text, index);
      if (!hit) throw new Error(`not found: ${selector} ${text ?? ""}`);
      await tv.click(hit.x, hit.y);
      return hit;
    },
    async waitFor(expression, timeout = 30000, step = 300) {
      const end = Date.now() + timeout;
      for (;;) {
        const value = await tv.eval(expression).catch(() => null);
        if (value) return value;
        if (Date.now() > end) throw new Error(`timed out waiting for: ${expression.slice(0, 120)}`);
        await sleep(step);
      }
    },
  };
  return tv;
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [command, ...args] = process.argv.slice(2);
  const tv = await connect();
  try {
    if (command === "shot") console.log(await tv.shot(args[0] || "shot.png"));
    else if (command === "eval") console.log(JSON.stringify(await tv.eval(args.join(" ")), null, 1));
    else if (command === "run") {
      const flow = (await import(pathToFileURL(resolve(args[0])).href)).default;
      const out = await flow(tv, args.slice(1));
      if (out !== undefined) console.log(typeof out === "string" ? out : JSON.stringify(out, null, 1));
    } else console.error("usage: cdp.mjs shot <png> | eval <js> | run <flow.mjs> [args]");
  } finally {
    tv.close();
  }
}
