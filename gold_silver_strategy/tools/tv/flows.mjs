// TradingView UI steps used by the test runs (see cdp.mjs). Each step does what a person
// would do with the mouse and keyboard in the logged-in tab, and reads what is on screen.

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const PINE = { strategy: resolve(HERE, "../../pine/GSMS_Strategy.pine"), indicator: resolve(HERE, "../../pine/GSMS_Indicator.pine") };
const X_ENV = { ...process.env, DISPLAY: process.env.DISPLAY || ":1" };
// xclip stays alive to serve the selection, so its output must not be piped when writing.
const setClipboard = (text) => execFileSync("xclip", ["-selection", "clipboard", "-i"], { input: text, env: X_ENV, stdio: ["pipe", "ignore", "ignore"] });
const getClipboard = () => execFileSync("xclip", ["-selection", "clipboard", "-o"], { env: X_ENV, maxBuffer: 1 << 26 }).toString();

const rectOf = (selector, filter = "true") => `(() => {
  const el = [...document.querySelectorAll(${JSON.stringify(selector)})].find((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && (${filter}); });
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height, cx: r.x + r.width / 2, cy: r.y + r.height / 2 };
})()`;

export async function dismissPopups(tv) {
  for (let i = 0; i < 5; i++) {
    const hit = await tv.find("button", "^Got it!?$");
    if (!hit) break;
    await tv.click(hit.x, hit.y);
    await tv.sleep(500);
  }
}

// An upgrade or sign-in dialog means the plan does not allow what was just tried. Never click through it.
export async function upgradeDialog(tv) {
  return tv.eval(`(() => {
    const dialog = [...document.querySelectorAll('[role="dialog"], [data-dialog-name], div[class*="dialog"]')].find((d) => d.getBoundingClientRect().width > 200 && /upgrade|your plan has a limit|start free trial|try free|plans? (start|from)/i.test(d.innerText || ""));
    return dialog ? dialog.innerText.replace(/\\s+/g, " ").slice(0, 400) : null;
  })()`);
}

export async function closeDialog(tv) {
  for (let i = 0; i < 4; i++) {
    await tv.key("Escape");
    await tv.sleep(700);
    if (!(await tv.eval(`!!document.querySelector('[role="dialog"], [data-name="indicator-properties-dialog"], [role="listbox"]')`))) break;
  }
}

export async function editorOpen(tv) {
  return !!(await tv.eval(rectOf(".monaco-editor.pine-editor-monaco")));
}

export async function openEditor(tv) {
  if (await editorOpen(tv)) return;
  await tv.clickOn('[data-name="pine-dialog-button"]');
  await tv.waitFor(rectOf(".monaco-editor.pine-editor-monaco"), 20000);
  await tv.sleep(1500);
}

export async function closeEditor(tv) {
  if (!(await editorOpen(tv))) return;
  const editor = await tv.eval(rectOf(".monaco-editor.pine-editor-monaco"));
  const hit = await tv.eval(rectOf('button[aria-label="Close"], [data-name="close"]', `r.x > ${editor.x} && r.y < ${editor.y}`));
  if (!hit) throw new Error("Pine Editor close button not found");
  await tv.click(hit.cx, hit.cy);
  await tv.sleep(1500);
}

async function focusEditor(tv) {
  const editor = await tv.eval(rectOf(".monaco-editor.pine-editor-monaco"));
  await tv.click(editor.x + editor.w * 0.5, editor.y + editor.h * 0.5);
  await tv.sleep(250);
  const tag = await tv.eval("document.activeElement.tagName");
  if (tag !== "TEXTAREA") throw new Error(`editor not focused (${tag})`);
}

export async function editorTitle(tv) {
  const editor = await tv.eval(rectOf(".monaco-editor.pine-editor-monaco"));
  return tv.eval(`(() => { const e = [...document.querySelectorAll('h2, [class*="title"]')].find((x) => { const r = x.getBoundingClientRect(); return r.width > 0 && r.x >= ${editor.x} - 5 && r.y < ${editor.y} && r.y > ${editor.y} - 70 && (x.innerText || "").trim().length > 0; }); return e ? e.innerText.trim() : null; })()`);
}

export async function editorText(tv) {
  await focusEditor(tv);
  setClipboard(" ");
  await tv.key("a", { ctrl: true, commands: ["selectAll"] });
  await tv.sleep(250);
  await tv.key("c", { ctrl: true, commands: ["copy"] });
  await tv.sleep(700);
  return getClipboard().replace(/\r\n/g, "\n");
}

// Replace the open script's source with a generated file; returns true when the editor then holds exactly that file.
export async function pasteSource(tv, kind) {
  const source = readFileSync(PINE[kind], "utf8");
  setClipboard(source);
  await focusEditor(tv);
  await tv.key("a", { ctrl: true, commands: ["selectAll"] });
  await tv.sleep(250);
  await tv.key("v", { ctrl: true, commands: ["paste"] });
  await tv.sleep(2500);
  const back = await editorText(tv);
  return back.trimEnd() === source.trimEnd();
}

export async function addScriptToChart(tv) {
  await openEditor(tv);
  const ok = await tv.eval(`(() => {
    const b = [...document.querySelectorAll("button")].find((e) => (e.innerText || "").trim() === "Add to chart" && e.getBoundingClientRect().width > 0);
    if (!b) return false;
    b.click();
    return true;
  })()`);
  if (!ok) throw new Error("Add to chart button not found");
  await tv.sleep(4000);
}

export async function removeGsms(tv) {
  const rect = await tv.eval(`(() => {
    const item = [...document.querySelectorAll('[data-name="legend-source-item"], [data-qa-id="legend-source-item"]')].find((e) => /GSMS/.test(e.innerText || ""));
    if (!item) return null;
    const ir = item.getBoundingClientRect();
    const b = [...item.querySelectorAll("button")].find((e) => /Remove/i.test(e.getAttribute("aria-label") || ""));
    if (!b) return null;
    const r = b.getBoundingClientRect();
    return { ix: ir.x + 40, iy: ir.y + ir.height / 2, x: r.x + r.width / 2, y: r.y + r.height / 2 };
  })()`);
  if (!rect) return false;
  await tv.move(rect.ix, rect.iy);
  await tv.sleep(400);
  await tv.click(rect.x, rect.y);
  await tv.sleep(1500);
  return true;
}

// The chart layout is what a reload restores. Click the header Save control when it is dirty.
export async function saveLayout(tv) {
  const hit = await tv.eval(`(() => {
    const b = [...document.querySelectorAll("button")].find((e) => {
      const r = e.getBoundingClientRect();
      const t = (e.innerText || "").trim();
      return r.y < 40 && r.width > 0 && t === "Save";
    });
    if (!b) return null;
    const r = b.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  })()`);
  if (!hit) return false;
  await tv.click(hit.x, hit.y);
  await tv.sleep(3000);
  return true;
}

export async function saveScript(tv) {
  // Ctrl+S saves the chart layout. The script is saved by the editor's own Save button.
  const hit = await tv.eval(`(() => {
    const b = [...document.querySelectorAll("button")].find((e) => (e.innerText || "").trim() === "Save" && e.getBoundingClientRect().y > 40 && e.getBoundingClientRect().y < 140 && e.getBoundingClientRect().width > 0);
    if (!b) return null;
    const r = b.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2, label: b.innerText };
  })()`);
  // No Save button means the editor has nothing new to store (the control then reads as saved).
  if (!hit) return;
  await tv.click(hit.x, hit.y);
  await tv.sleep(4000);
}

export async function legend(tv) {
  return tv.eval(`[...document.querySelectorAll('[data-name="legend-source-item"], [data-qa-id="legend-source-item"]')].map((e) => (e.innerText || "").replace(/\\s+/g, " ").trim().slice(0, 200))`);
}

// Everything the Strategy Tester overview shows, as text, plus the parsed headline figures.
export async function readTester(tv) {
  const text = await tv.eval(`(() => {
    let e = [...document.querySelectorAll("*")].find((x) => (x.innerText || "").trim() === "Key stats");
    let hops = 0;
    while (e && hops < 12 && !(e.innerText || "").includes("Trades analysis")) { e = e.parentElement; hops++; }
    return e ? e.innerText : null;
  })()`);
  const range = await tv.eval(`(() => { const b = [...document.querySelectorAll("button, [role=button]")].find((x) => /\\d{4}\\s*[—-]\\s*\\w+ \\d+, \\d{4}/.test(x.innerText || "") && x.getBoundingClientRect().width > 0); return b ? b.innerText.replace(/\\s+/g, " ").trim() : null; })()`);
  const empty = await tv.eval(`!![...document.querySelectorAll("*")].find((x) => /^(Trades are required to see strategy report|No data)/.test((x.innerText || "").trim()) && x.children.length === 0)`);
  if (!text) return { range, empty, text: null };
  const lines = text.split("\n").map((s) => s.trim()).filter(Boolean);
  const after = (label, n = 1) => { const i = lines.indexOf(label); return i >= 0 ? lines[i + n] : null; };
  const num = (s) => (s == null ? null : Number(s.replace(/−/g, "-").replace(/[,+\s]|USD|%/g, "")));
  const pair = (s) => { if (!s) return [null, null]; const m = s.replace(/−/g, "-").match(/^([+-]?[\d,.]+)\s*USD\s*([+-]?[\d,.]+)%$/); return m ? [num(m[1]), num(m[2])] : [null, null]; };
  const [pnl, pnlPct] = pair(after("Total PnL"));
  const [dd, ddPct] = pair(after("Max drawdown"));
  const [grossProfit] = pair(after("Gross profit"));
  const [grossLoss] = pair(after("Gross loss"));
  const prof = (after("Profitable trades") || "").match(/^([\d.]+)%\s*(\d+)\/(\d+)$/);
  const sideValue = (label) => { const i = lines.indexOf(label, lines.indexOf("By side")); return i >= 0 ? num(lines[i + 1]) : null; };
  return {
    range, empty, text,
    net_profit_usd: pnl, net_profit_pct: pnlPct, max_drawdown_usd: dd, max_drawdown_pct: ddPct,
    percent_profitable: prof ? Number(prof[1]) : null, winning_trades: prof ? Number(prof[2]) : null, total_trades: prof ? Number(prof[3]) : null,
    profit_factor: num(after("Profit factor")), gross_profit_usd: grossProfit, gross_loss_usd: grossLoss,
    long_pnl_usd: sideValue("Long"), short_pnl_usd: sideValue("Short"),
    expectancy_usd: pair(after("Expectancy"))[0], avg_profit_pct: num(after("Average profit")), avg_loss_pct: num(after("Average loss")),
  };
}

export async function chartHeader(tv) {
  return tv.eval(`(() => { const t = document.querySelector('[data-name="legend-series-item"], [data-qa-id="legend-series-item"]'); return t ? t.innerText.replace(/\\s+/g, " ").trim().slice(0, 120) : null; })()`);
}

// ── List of trades ──
// The tester's two views are tabs: light-tab-0 = overview, light-tab-1 = list of trades.
export async function showOverview(tv) {
  await tv.clickOn('[data-name="light-tab-0"]');
  await tv.sleep(1500);
}

const TRADE_ROWS = `(() => {
  const tab = document.querySelector('[data-name="light-tab-1"]');
  let root = tab;
  for (let i = 0; i < 8; i++) root = root.parentElement;
  const text = root.innerText.replace(/−/g, "-");
  const out = [];
  const re = /(\\d+)(Long|Short)\\s+Exit\\s+Entry\\s+([A-Z][a-z]{2} \\d+, \\d{4}, \\d\\d:\\d\\d)\\s+([A-Z][a-z]{2} \\d+, \\d{4}, \\d\\d:\\d\\d)\\s+([\\d,.]+)\\s+USD\\s+([\\d,.]+)\\s+USD\\s+([\\d,.]+)\\s+[\\d,.]+\\s*[KMB]?\\s*USD\\s+([+-]?[\\d,.]+)\\s+USD/g;
  let m;
  while ((m = re.exec(text))) out.push({ n: +m[1], side: m[2], exit_time: m[3], entry_time: m[4], exit_price: +m[5].replace(/,/g, ""), entry_price: +m[6].replace(/,/g, ""), size: +m[7].replace(/,/g, ""), net_pnl: +m[8].replace(/,/g, "") });
  const open = (text.match(/(\\d+)(Long|Short)\\s+Open\\s+Entry/) || []).slice(1, 3);
  const scroller = [...root.querySelectorAll("*")].find((e) => e.scrollHeight > e.clientHeight + 20 && getComputedStyle(e).overflowY !== "visible" && e.clientHeight > 80);
  return { rows: out, open, canScroll: !!scroller, top: scroller ? scroller.scrollTop : 0, max: scroller ? scroller.scrollHeight - scroller.clientHeight : 0 };
})()`;

// All closed trades shown in the list (scrolls through it, the list only renders what is visible).
export async function readTrades(tv) {
  await tv.clickOn('[data-name="light-tab-1"]');
  await tv.sleep(2500);
  const scroll = (how) => tv.eval(`(() => {
      const tab = document.querySelector('[data-name="light-tab-1"]');
      let root = tab;
      for (let i = 0; i < 8; i++) root = root.parentElement;
      const s = [...root.querySelectorAll("*")].find((e) => e.scrollHeight > e.clientHeight + 20 && getComputedStyle(e).overflowY !== "visible" && e.clientHeight > 80);
      if (s) s.scrollTop = ${how === "top" ? "0" : "s.scrollTop + Math.max(100, s.clientHeight * 0.7)"};
    })()`);
  await scroll("top");
  await tv.sleep(600);
  const seen = new Map();
  let open = [];
  for (let step = 0; step < 400; step++) {
    const page = await tv.eval(TRADE_ROWS);
    for (const row of page.rows) seen.set(row.n, row);
    if (page.open.length) open = page.open;
    if (!page.canScroll || page.top >= page.max - 2) break;
    await scroll("down");
    await tv.sleep(350);
  }
  return { trades: [...seen.values()].sort((a, b) => a.n - b.n), open };
}

// ── Chart, settings dialog, waiting ──
const DIALOG = '[data-name="indicator-properties-dialog"]';

export async function loadChart(tv, symbol, interval) {
  const base = await tv.eval("location.origin + location.pathname");
  await tv.send("Page.enable");
  tv.navigate(`${base}?symbol=${encodeURIComponent(symbol)}&interval=${interval}`).catch(() => {});
  await tv.sleep(1500);
  await tv.send("Page.handleJavaScriptDialog", { accept: true }).catch(() => {});
  await tv.waitFor(`[...document.querySelectorAll('[data-name="legend-source-item"], [data-qa-id="legend-source-item"]')].some((e) => /GSMS/.test(e.innerText || ""))`, 90000, 500);
  await tv.sleep(2500);
  await dismissPopups(tv);
}

const busy = `[...document.querySelectorAll("*")].some((e) => e.children.length === 0 && (e.innerText || "").trim() === "Updating report" && e.getBoundingClientRect().width > 0)`;

// Wait until the Strategy Tester shows a finished report (or its "no trades" message) that no longer changes.
export async function waitTester(tv, timeout = 120000) {
  const end = Date.now() + timeout;
  let last = null;
  let stable = 0;
  await tv.sleep(2500);
  while (Date.now() < end) {
    const report = await readTester(tv);
    const key = JSON.stringify([report.range, report.empty, report.net_profit_usd, report.total_trades, report.max_drawdown_usd]);
    const ready = !(await tv.eval(busy)) && (report.empty || report.total_trades != null);
    stable = ready && key === last ? stable + 1 : 0;
    if (stable >= 2) return report;
    last = key;
    await tv.sleep(1500);
  }
  throw new Error("Strategy Tester did not settle");
}

export async function openSettings(tv) {
  if (await tv.eval(`!!document.querySelector('${DIALOG}')`)) return;
  const item = await tv.eval(`(() => { const e = [...document.querySelectorAll('[data-name="legend-source-item"], [data-qa-id="legend-source-item"]')].find((x) => /GSMS/.test(x.innerText || "")); if (!e) return null; const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, h: r.height }; })()`);
  if (!item) throw new Error("GSMS is not on the chart");
  await tv.click(item.x + 30, item.y + item.h / 2, 2);
  await tv.waitFor(`!!document.querySelector('${DIALOG}')`, 15000);
  await tv.sleep(700);
}

async function tab(tv, name) {
  await tv.clickOn(`${DIALOG} [role="tab"], ${DIALOG} button`, `^${name}$`);
  await tv.sleep(900);
}

async function centre(tv, expression) {
  return tv.eval(`(() => { const e = ${expression}; if (!e) return null; e.scrollIntoView({ block: "center" }); const r = e.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()`);
}

// config: { preset, modules: {"EMA crossover": true, ...}, commission }
export async function applyConfig(tv, config) {
  await openSettings(tv);
  await tab(tv, "Inputs");
  const current = await tv.eval(`document.querySelector('${DIALOG} #in_0').innerText.trim()`);
  if (config.preset && current !== config.preset) {
    const combo = await centre(tv, `document.querySelector('${DIALOG} #in_0')`);
    await tv.sleep(300);
    await tv.click(combo.x, combo.y);
    await tv.sleep(900);
    await tv.clickOn('[role="option"]', `^${config.preset.replace(/[()]/g, "\\$&")}$`);
    await tv.sleep(700);
  }
  for (const [label, wanted] of Object.entries(config.modules || {})) {
    const box = `[...document.querySelectorAll('${DIALOG} input[type="checkbox"]')].find((i) => (i.closest("label")?.innerText || "").trim() === ${JSON.stringify(label)})`;
    if ((await tv.eval(`${box}.checked`)) !== wanted) {
      const hit = await centre(tv, `${box}.closest("label")`);
      await tv.sleep(300);
      await tv.click(hit.x, hit.y);
      await tv.sleep(400);
      if ((await tv.eval(`${box}.checked`)) !== wanted) throw new Error(`could not set ${label}`);
    }
  }
  const applied = {
    preset: await tv.eval(`document.querySelector('${DIALOG} #in_0').innerText.trim()`),
    modules: await tv.eval(`Object.fromEntries([...document.querySelectorAll('${DIALOG} input[type="checkbox"]')].slice(0, 4).map((i) => [i.closest("label").innerText.trim(), i.checked]))`),
  };
  if (config.commission != null) {
    await tab(tv, "Properties");
    await tv.waitFor(`document.querySelectorAll('${DIALOG} input:not([type="checkbox"])').length >= 7 && /Commission/.test(document.querySelector('${DIALOG}').innerText)`, 10000);
    // Text inputs on this tab, in order: initial capital, order size, pyramiding, commission, leverage x2, slippage.
    const field = `[...document.querySelectorAll('${DIALOG} input:not([type="checkbox"])')][3]`;
    if (Number(await tv.eval(`${field}.value`)) !== config.commission) {
      const hit = await centre(tv, field);
      await tv.sleep(300);
      await tv.click(hit.x, hit.y);
      await tv.key("a", { ctrl: true, commands: ["selectAll"] });
      await tv.type(String(config.commission));
      await tv.key("Tab");
      await tv.sleep(500);
    }
    applied.commission = Number(await tv.eval(`${field}.value`));
    applied.commission_type = await tv.eval(`(() => { const t = document.querySelector('${DIALOG}').innerText; const m = t.match(/Commission\\s+(\\w+)/); return m ? m[1] : null; })()`);
    if (applied.commission !== config.commission) throw new Error(`commission is ${applied.commission}, wanted ${config.commission}`);
  }
  await tv.clickOn(`${DIALOG} button`, "^Ok$");
  await tv.sleep(1200);
  return applied;
}
