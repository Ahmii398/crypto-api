// Loads the rebuilt GSMS strategy and records Strategy Tester results for Auto (by timeframe)
// on XAUUSD. The preset reads the chart timeframe, so each run only changes the interval.
//
//   node cdp.mjs run run_gold_auto.mjs

import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import * as f from "./flows.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(HERE, "../../results/tradingview");
const INTERVAL = { "1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "2h": "120", "4h": "240", "1D": "1D" };

export default async (tv, args) => {
  mkdirSync(resolve(OUT, "runs"), { recursive: true });
  mkdirSync(resolve(OUT, "screenshots"), { recursive: true });
  await f.closeDialog(tv);
  if (!args.includes("--skip-paste")) {
    await f.openEditor(tv);
    const pasted = await f.pasteSource(tv, "strategy");
    console.log(`paste verified: ${pasted}`);
    if (!pasted) throw new Error("editor contents do not match pine/GSMS_Strategy.pine");
    await f.saveScript(tv);
  }
  const step = (line) => { appendFileSync("/tmp/tv_steps.log", line + "\n"); console.log(line); };
  await f.openEditor(tv);
  let legend = await f.legend(tv);
  step("legend before: " + (legend.find((l) => /GSMS/.test(l)) || "none").slice(0, 140));
  for (let n = 0; n < 4 && !/Auto \(by timeframe\)/.test(legend.join(" ")); n++) {
    const removed = await f.removeGsms(tv);
    step(`remove attempt ${n} -> ${removed}`);
    if (!removed) break;
    legend = await f.legend(tv);
  }
  if (!/Auto \(by timeframe\)/.test((await f.legend(tv)).join(" "))) {
    await f.addScriptToChart(tv);
    step("added to chart");
  }
  await f.closeEditor(tv);
  const savedLayout = await f.saveLayout(tv);
  console.log(`layout saved: ${savedLayout}`);
  legend = await f.legend(tv);
  console.log("legend", (legend.find((l) => /GSMS/.test(l)) || "").slice(0, 160));
  const log = [];
  for (const [timeframe, interval] of Object.entries(INTERVAL)) {
    const name = `XAUUSD_${timeframe}_auto`;
    try {
      await f.loadChart(tv, "OANDA:XAUUSD", interval);
      const applied = await f.applyConfig(tv, { preset: "Auto (by timeframe)", modules: { "EMA crossover": true, "Liquidity sweep": true, "Supply / demand zones": true, "Candlestick patterns": true }, commission: 0.0175 });
      await f.showOverview(tv);
      const report = await f.waitTester(tv);
      const header = await f.chartHeader(tv);
      const legend = (await f.legend(tv)).find((l) => /GSMS/.test(l)) || null;
      await tv.shot(resolve(OUT, "screenshots", `${name}.jpg`));
      const list = report.empty ? { trades: [], open: [] } : await f.readTrades(tv);
      if (!report.empty) await f.showOverview(tv);
      const record = {
        symbol: "XAUUSD", timeframe, config: "auto", tradingview_symbol: "OANDA:XAUUSD", recorded_utc: new Date().toISOString(),
        applied, chart_header: header, legend, tester: { ...report, text: undefined }, tester_text: report.text, trades: list.trades, open_trade: list.open,
      };
      writeFileSync(resolve(OUT, "runs", `${name}.json`), JSON.stringify(record, null, 1));
      const line = `${name}: ${report.range} | trades ${report.total_trades ?? 0} (list ${list.trades.length}) | net ${report.net_profit_usd ?? 0} | PF ${report.profit_factor ?? "-"} | legend ${legend}`;
      log.push(line);
      console.log(line);
    } catch (error) {
      console.log(`${name}: FAILED ${error.message}`);
      await tv.shot(resolve(OUT, "screenshots", `${name}_FAILED.jpg`)).catch(() => {});
      await f.closeDialog(tv).catch(() => {});
    }
  }
  return log.join("\n");
};
