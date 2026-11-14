// Records Strategy Tester results for the Auto preset already on the XAUUSD chart.
// Changes the chart interval from the toolbar, so the page is not reloaded.

import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import * as f from "./flows.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(HERE, "../../results/tradingview");
const INTERVALS = [
  ["1m", "1"],
  ["5m", "5"],
  ["15m", "15"],
  ["30m", "30"],
  ["1h", "60"],
  ["2h", "120"],
  ["4h", "240"],
  ["1D", "1D"],
];
const step = (line) => { appendFileSync("/tmp/tv_steps.log", line + "\n"); console.log(line); };

async function setInterval(tv, code) {
  const opener = await tv.eval(`(() => {
    const b = [...document.querySelectorAll("button")].find((e) => /^Change interval/.test(e.getAttribute("aria-label") || "") && e.getBoundingClientRect().y < 90 && e.getBoundingClientRect().width > 0);
    if (!b) return null;
    const r = b.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  })()`);
  if (!opener) throw new Error("interval button not found");
  await tv.click(opener.x, opener.y);
  await tv.sleep(600);
  const field = await tv.eval(`(() => {
    const input = [...document.querySelectorAll("input")].find((e) => e.getBoundingClientRect().width > 20 && e.getBoundingClientRect().height > 0 && e.getBoundingClientRect().y > 60);
    if (!input) return null;
    const r = input.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  })()`);
  if (!field) throw new Error("interval field not found");
  await tv.click(field.x, field.y);
  await tv.sleep(200);
  await tv.key("a", { ctrl: true, commands: ["selectAll"] });
  await tv.type(code);
  await tv.key("Enter");
  await tv.sleep(2000);
}

export default async (tv) => {
  mkdirSync(resolve(OUT, "runs"), { recursive: true });
  mkdirSync(resolve(OUT, "screenshots"), { recursive: true });
  await f.closeDialog(tv);
  await f.closeEditor(tv);
  await tv.key("Escape");
  await tv.sleep(300);
  await tv.eval(`(() => { const b = [...document.querySelectorAll("button")].find((e) => /Save all charts/.test(e.getAttribute("aria-label") || "")); if (b) b.click(); })()`);
  step("layout save clicked");
  for (const [timeframe, label] of INTERVALS) {
    const name = `XAUUSD_${timeframe}_auto`;
    try {
      await setInterval(tv, label);
      const applied = await f.applyConfig(tv, { preset: "Auto (by timeframe)", modules: { "EMA crossover": true, "Liquidity sweep": true, "Supply / demand zones": true, "Candlestick patterns": true }, commission: 0.0175 });
      await f.showOverview(tv);
      const report = await f.waitTester(tv, 90000);
      const legend = (await f.legend(tv)).find((l) => /GSMS/.test(l)) || null;
      await tv.shot(resolve(OUT, "screenshots", `${name}.jpg`));
      // The trade list is virtualised. Record it when it is short enough to scroll through;
      // the overview figures are the Strategy Tester's own totals either way.
      const list = report.empty || (report.total_trades || 0) > 40 ? { trades: [], open: [] } : await f.readTrades(tv);
      if (!report.empty) await f.showOverview(tv);
      writeFileSync(resolve(OUT, "runs", `${name}.json`), JSON.stringify({
        symbol: "XAUUSD", timeframe, config: "auto", tradingview_symbol: "OANDA:XAUUSD", recorded_utc: new Date().toISOString(),
        applied, legend, tester: { ...report, text: undefined }, tester_text: report.text, trades: list.trades, open_trade: list.open,
      }, null, 1));
      step(`${name}: ${report.range} | trades ${report.total_trades ?? 0} (list ${list.trades.length}) | net ${report.net_profit_usd ?? 0} | PF ${report.profit_factor ?? "-"}`);
    } catch (error) {
      step(`${name}: FAILED ${error.message}`);
      await tv.shot(resolve(OUT, "screenshots", `${name}_FAILED.jpg`)).catch(() => {});
      await f.closeDialog(tv).catch(() => {});
      await tv.key("Escape");
    }
  }
};
