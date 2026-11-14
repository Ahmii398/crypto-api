# GSMS trading rules

One specification, three implementations that are tested against each other:
`pine/GSMS_Indicator.pine`, `pine/GSMS_Strategy.pine` (both generated from
`pine/src/gsms.template.pine`) and the Python engine `python/gsms/engine.py`.

Everything is evaluated on closed bars. A signal on the close of bar *t* becomes a market
order filled at the open of bar *t+1*. Nothing looks at later bars, and nothing is redrawn.

## 1. Building blocks

- **EMA(n)**: `e[0] = close[0]`, `e[t] = a*close[t] + (1-a)*e[t-1]`, `a = 2/(n+1)`.
- **ATR(n)** (Wilder): true range, `atr[0] = high[0]-low[0]`, `atr[t] = (atr[t-1]*(n-1) + tr[t])/n`. Default n = 14.
- **Swing low of length L**: a bar whose low is strictly below the lows of the L bars before
  and the L bars after it. It is known L bars later, and only used from then on. Swing highs
  mirror this.

## 2. The four modules

Each module produces a bullish and a bearish event per bar.

### EMA (fast / slow)
- *Cross* role: event when the fast EMA crosses the slow EMA. It stays "in context" while the
  cross is at most `window-1` bars old and the fast EMA is still on that side.
- *Trend* role: no event; longs need fast > slow, shorts fast < slow.

### Liquidity sweep
Resting liquidity levels:
- swing highs and lows of length `ls_swing` (the 10 most recent per side, dropped after `ls_max_age` bars),
- optionally the previous day's high/low and the previous week's high/low. A day is the
  18:00 to 17:00 New York session, as on TradingView's metal charts.

Bullish sweep of a low (bearish sweeps mirror it):
- *Wick*: a bar trades below the level and closes back above it. A bar that trades below and
  closes below uses the level up without a signal.
- *Close confirm*: a bar trades below the level; within `ls_reclaim` further bars a bar must
  close above the level with close > open.

A level gives at most one signal.

### Supply / demand zones
A demand zone is created when a swing low of length `sd_swing` is confirmed and
- the highest high since the swing is at least `sd_strength` x ATR above the zone top, and
- the confirming bar closes above the zone top, and
- the zone does not overlap an existing demand zone.

Zone = [swing low, swing low + `sd_width` x ATR]. At most 8 zones per side; a zone is dropped
after `sd_max_age` bars or when invalidated (*Close beyond*: a close below the zone;
*Wick beyond*: any trade below it).

- A **touch** is counted each time price enters the zone from outside. The zone is tradable
  while its touch count is at most `sd_max_touch` (1 = fresh zones only).
- Event, *Touch*: the bar trades inside a tradable zone.
- Event, *Rejection close*: additionally the bar closes above the zone top with close > open.
- The module is in context for `window-1` bars after a touch; an invalidation cancels it.

Supply zones mirror all of this at swing highs.

### Candlestick patterns
Any combination of engulfing, pin bar (hammer / shooting star), morning / evening star and
piercing line / dark cloud cover. Sizes are measured in ATR; *strict* raises every threshold
(see the constants in the template). With *confirmation candle* on, the event is moved to the
next bar and requires it to close beyond the pattern bar's high (bullish) or low (bearish).

## 3. Confluence

A long signal needs, on the same closed bar:
1. every enabled module in bullish context (event at most `window-1` bars ago), and
2. at least one enabled module's bullish event on this bar, and
3. the filters: direction, trend EMA (close above it for longs), session hours (UTC, by bar open), date window, warm-up, and
4. no open position and no pending entry.

If the long and the short conditions are both met, no trade is taken.

## 4. Stop, target, position size

Fixed when the signal fires, from the signal bar's close `C`:
- *ATR*: stop distance = `sl_atr` x ATR.
- *Structure*: stop beyond the lowest low (highest high) of the last `sl_lookback` bars, plus `sl_buffer` x ATR.
- *Zone*: stop beyond the demand (supply) zone that was touched, plus the buffer; falls back to
  *Structure* when the supply/demand module is off.
- The distance is clamped to [`min_stop`, `max_stop`] x ATR.
- Target = `rr` x stop distance. Stop and target prices are rounded to the tick.
- Size = risk per trade / stop distance (fixed cash risk).

## 5. Fills (TradingView broker-emulator rules)

- Entry at the next bar's open. Stop and target are live from that bar.
- A bar that opens beyond the stop or the target exits at the open.
- If one bar reaches both, the extreme nearer to the open is assumed to be hit first.
- One position at a time; a new signal can fire on the bar in which the previous trade closed.

## 6. Costs

Charged as a percentage of traded value on every fill (TradingView "commission, percent"):
XAUUSD 0.0175 %, XAGUSD 0.08 % per fill. These cover half the measured bid/ask spread plus
commission and slippage; see `results/data_quality.md` and `python/gsms/backtest.py`.
Results are expressed in R (the amount risked per trade).
