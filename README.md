# cbbtc-race-bot

[![make.fun · btc-race-24h-20261123-0511](https://make.fun/badge/btc-race-24h-20261123-0511.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)
[![rank](https://make.fun/badge/btc-race-24h-20261123-0511/verad-xe/cbbtc-race-bot/rank.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)
[![reward](https://make.fun/badge/btc-race-24h-20261123-0511/verad-xe/cbbtc-race-bot/reward.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)

Entered in [make.fun's "Race a BTC trading bot live for 24 hours" bounty](https://make.fun/bounty/btc-race-24h-20261123-0511).

A selective trend-following bot for the make.fun competition
`btc-race-24h-20261123-0511` ("Race a BTC trading bot live for 24 hours").
It trades cbBTC/USDC on 1-minute bars, long only, starting from 1,000 USDC
of paper money.

## Strategy

- **Enter long only on a confirmed trend.** The fast EMA (15 bars) must be
  above the slow EMA (60 bars), the close above the 4-hour trend EMA
  (240 bars), and momentum must confirm on two horizons: the 60-minute
  return above +0.80% and the 120-minute return above +0.80%. A full-size
  round trip costs roughly 0.46% in fees, slippage and pool impact, so a
  trade has to catch a move well above that; the double momentum filter is
  what keeps the bot out of range-bound chop, where 1-minute noise
  dominates.
- **First entry vs later entries.** The first entry of the window may fire
  on the trend state alone (a fresh EMA cross and a strong momentum print
  rarely land on the same minute). Later entries need a fresh cross of the
  fast EMA above the slow one, so after an exit the bot cannot keep
  re-buying the same chop.
- **Exits protect the win.** The whole position is sold when the 30-bar EMA
  crosses back below the 90-bar EMA, when the position gives back 2.5%
  from its peak value (trailing stop), or when it falls 3.0% below its
  entry cost (hard stop). A 15-bar minimum hold and a 30-bar cooldown after
  every exit keep single noisy minutes from shaking it out.
- **Guardrail insurance.** If it still has not traded by bar 1200 it enters
  on a mild uptick; if it still has not traded by bar 1380 it buys half
  size regardless, so the entry always meets the "at least 1 trade"
  guardrail.

## Dev-window result (local protocol replay)

Net PnL **+14.23 USDC** on the frozen 24h dev window (1 round trip,
2 trades, max drawdown 1.81%), vs +32.17 USDC for buy-and-hold on the same
window. Verified by replaying the bot through the real `bars-replay/v1`
protocol (two runs, byte-identical replies) with the harness's exact fill
model; `grade-local` itself could not be run here because its process
driver runs Node bots only and this VM has no Docker for the Python run.
The bot deliberately gives up part of a strong uptrend in exchange
for staying out of chop and downtrends: on the dev window's range-bound
second half it makes no further trade after exiting the morning move.
Standalone, the second half loses $9.06 on one round trip (max drawdown
1.13%), and a feed with 5 bars dropped still completes at +$13.18.

## Layout

- `main.py` — the bot (Python 3.13, standard library only). Speaks
  `agent-fun/bars-replay/v1` through the vendored shim.
- `vendor/makefun_bars.py` — byte-identical copy of the kit's Python shim.
- `LICENSE` — MIT. `.makefun/owner.json` — payout wallet claim file.

## Run locally

```sh
node <kit>/cli/makefun.mjs grade-local --kit <kit> --bot . --driver docker
```

(The process driver runs Node bots only; a Python bot needs the Docker
driver, i.e. the server's sandbox.)
