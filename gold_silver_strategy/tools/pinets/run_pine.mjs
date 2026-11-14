// Run a Pine script in PineTS on bars supplied by the Python side and dump what it produced.
//
//   node run_pine.mjs <job.json>
//
// job.json: { pine, out, symbol, timeframe, mintick, bars: {chart, D, W}, plots: [names] }
// `bars` are files holding [{openTime, closeTime, open, high, low, close, volume}] in ms UTC.
// The daily and weekly files are the session-aligned bars built by gsms.data, so
// request.security("D"/"W") sees the same higher-timeframe candles as the Python engine.
import fs from 'fs';
import { PineTS } from 'pinets';

const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const load = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));
const series = { chart: load(job.bars.chart), D: load(job.bars.D), W: load(job.bars.W) };

const kind = (timeframe) => {
    const tf = String(timeframe).toUpperCase();
    if (tf === 'D' || tf === '1D') return 'D';
    if (tf === 'W' || tf === '1W') return 'W';
    if (tf === String(job.timeframe).toUpperCase()) return 'chart';
    throw new Error(`no data for timeframe ${timeframe}`);
};

const provider = {
    configure() {},
    async getMarketData(tickerId, timeframe, limit, sDate, eDate) {
        let rows = series[kind(timeframe)];
        if (sDate) rows = rows.filter((r) => r.openTime >= sDate);
        if (eDate) rows = rows.filter((r) => r.openTime <= eDate);
        if (limit && rows.length > limit) rows = rows.slice(rows.length - limit);
        return rows;
    },
    async getSymbolInfo() {
        return {
            ticker: job.symbol, tickerid: `OANDA:${job.symbol}`, prefix: 'OANDA', root: job.symbol, description: job.symbol,
            type: 'cfd', currency: 'USD', basecurrency: job.symbol.slice(0, 3), timezone: 'America/New_York', session: '1800-1700',
            mintick: job.mintick, minmove: 1, pricescale: Math.round(1 / job.mintick), pointvalue: 1, mincontract: 0.000001, volumetype: 'base',
        };
    },
};

const pine = new PineTS(provider, job.symbol, job.timeframe);
const started = Date.now();
const ctx = await pine.run(fs.readFileSync(job.pine, 'utf8'));

const trade = (t) => ({
    entry_id: t.entry_id, entry_bar: t.entry_bar_index, entry_time: t.entry_time, entry_price: t.entry_price,
    exit_id: t.exit_id, exit_bar: t.exit_bar_index, exit_time: t.exit_time, exit_price: t.exit_price,
    size: t.size, profit: t.profit, commission: t.commission,
});
const strategy = ctx.strategy ?? {};
const plots = {};
for (const name of job.plots ?? []) {
    const plot = ctx.plots[name];
    if (!plot) throw new Error(`plot not found: ${name}`);
    plots[name] = plot.data.map((d) => (d.value === true ? 1 : d.value === false || d.value == null || Number.isNaN(d.value) ? null : Number(d.value)));
}
fs.writeFileSync(job.out, JSON.stringify({
    pinets_version: JSON.parse(fs.readFileSync(new URL('./node_modules/pinets/package.json', import.meta.url), 'utf8')).version,
    bars: series.chart.length,
    seconds: (Date.now() - started) / 1000,
    warnings: ctx.warnings ?? [],
    closed: (strategy.closedtrades ?? []).map(trade),
    open: (strategy.opentrades ?? []).map(trade),
    netprofit: strategy.netprofit ?? null,
    plots,
}));
