// Runs the GSMS strategy through TradingView's Strategy Tester for every symbol, timeframe and
// configuration, and records what the tester shows (overview figures, list of trades, screenshot).
//
//   node cdp.mjs run run_matrix.mjs [XAUUSD|XAGUSD ...] [--tf 1h,4h] [--force]
//
// Prerequisites: the strategy is saved in the account and is on the saved chart layout.

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import * as f from "./flows.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(HERE, "../../results/tradingview");
const INTERVAL = { "5m": "5", "15m": "15", "30m": "30", "1h": "60", "2h": "120", "4h": "240", "1D": "1D" };
const COMMISSION = { XAUUSD: 0.0175, XAGUSD: 0.08 };
const MODULES = ["EMA crossover", "Liquidity sweep", "Supply / demand zones", "Candlestick patterns"];
const all = (on) => Object.fromEntries(MODULES.map((m, i) => [m, Array.isArray(on) ? on[i] : on]));
const CUSTOM = "Custom (inputs below)";

function configs(symbol, timeframe, presets) {
  const list = [{ key: "default_all_modules", preset: CUSTOM, modules: all(true) }];
  if (timeframe === "1h") {
    ["ema_only", "liquidity_sweep_only", "supply_demand_only", "candles_only"].forEach((key, i) => list.push({ key, preset: CUSTOM, modules: all(MODULES.map((_, j) => i === j)) }));
  }
  for (const preset of presets.filter((p) => p.symbol === symbol)) list.push({ key: "preset_" + preset.name.toLowerCase().replace(/\s+/g, "_"), preset: preset.name, modules: all(true) });
  return list;
}

export default async (tv, args) => {
  const force = args.includes("--force");
  const tfArg = args.includes("--tf") ? args[args.indexOf("--tf") + 1].split(",") : Object.keys(INTERVAL);
  const symbols = args.filter((a) => /^XA[UG]USD$/.test(a));
  const presets = JSON.parse(readFileSync(resolve(HERE, "../../results/final_configs.json"), "utf8")).presets;
  mkdirSync(resolve(OUT, "runs"), { recursive: true });
  mkdirSync(resolve(OUT, "screenshots"), { recursive: true });
  const log = [];
  for (const symbol of symbols.length ? symbols : ["XAUUSD", "XAGUSD"]) {
    for (const timeframe of tfArg) {
      const todo = configs(symbol, timeframe, presets).filter((c) => force || !existsSync(resolve(OUT, "runs", `${symbol}_${timeframe}_${c.key}.json`)));
      if (!todo.length) continue;
      await f.loadChart(tv, `OANDA:${symbol}`, INTERVAL[timeframe]);
      for (const config of todo) {
        const name = `${symbol}_${timeframe}_${config.key}`;
        try {
          const applied = await f.applyConfig(tv, { ...config, commission: COMMISSION[symbol] });
          await f.showOverview(tv);
          const report = await f.waitTester(tv);
          const blocked = await f.upgradeDialog(tv);
          if (blocked) { await f.closeDialog(tv); }
          const header = await f.chartHeader(tv);
          const legend = (await f.legend(tv)).find((l) => /GSMS/.test(l)) || null;
          await tv.shot(resolve(OUT, "screenshots", `${name}.jpg`));
          const list = report.empty ? { trades: [], open: [] } : await f.readTrades(tv);
          if (!report.empty) await f.showOverview(tv);
          const record = {
            symbol, timeframe, config: config.key, tradingview_symbol: `OANDA:${symbol}`, recorded_utc: new Date().toISOString(),
            applied, chart_header: header, legend, upgrade_dialog: blocked,
            tester: { ...report, text: undefined }, tester_text: report.text, trades: list.trades, open_trade: list.open,
          };
          writeFileSync(resolve(OUT, "runs", `${name}.json`), JSON.stringify(record, null, 1));
          const line = `${name}: ${report.range} | trades ${report.total_trades ?? 0} (list ${list.trades.length}) | net ${report.net_profit_usd ?? 0} | PF ${report.profit_factor ?? "-"}`;
          log.push(line);
          console.log(line);
        } catch (error) {
          const line = `${name}: FAILED ${error.message}`;
          log.push(line);
          console.log(line);
          await tv.shot(resolve(OUT, "screenshots", `${name}_FAILED.jpg`)).catch(() => {});
          await f.closeDialog(tv).catch(() => {});
        }
      }
    }
  }
  return `${log.length} runs`;
};
