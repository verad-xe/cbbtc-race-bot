"""CBBTC 24h race bot: a selective trend-follower for cbBTC/USDC 1-minute bars.

Strategy (designed from the dev window's price action, not fitted to it):
  * It waits for a genuine trend, not for noise. It enters long only when the
    fast EMA (15 bars) is above the slow EMA (60 bars), the close is above the
    long trend EMA (240 bars, 4 hours), the 60-minute return is strong
    (> 0.80%) and the 120-minute return confirms it (> 0.80%). A full-size
    round trip costs roughly 0.46% in fees, slippage and pool impact, so a
    trade has to catch a move well above that; the double momentum filter is
    what keeps it out of range-bound chop, where 1-minute noise dominates.
  * The first entry of the window may fire on the trend state alone (a fresh
    EMA cross and a strong momentum print rarely land on the same minute, so
    demanding both would miss the move). Later entries need a fresh cross of
    the fast EMA above the slow one, so after an exit the bot cannot keep
    re-buying the same chop.
  * Exits protect the win: the whole position is sold when the 30-bar EMA
    crosses back below the 90-bar EMA, when the position gives back 2.5% from
    its peak value (trailing stop), or when it falls 3.0% below its entry
    cost (hard stop). A 15-bar minimum hold keeps a single noisy minute from
    shaking it out, and a 30-bar cooldown follows every exit.
  * Guardrail insurance: if it still has not traded by bar 1200 it enters on
    a mild uptick (close above the trend EMA, 60-minute return above -0.50%);
    if it still has not traded by bar 1380 it buys half size regardless, so
    the entry always meets the "at least 1 trade" guardrail. The final
    flatten at the end of the window closes whatever is held.

Everything is deterministic: plain float EMAs (the same arithmetic every
run), no clock, no randomness, no network, no files outside the repo. One
symbol, at most one open position, at most one order per reply.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOL = "CBBTC"

# --- trend / momentum parameters (bars are 1 minute) ---
FAST_SPAN = 15          # fast EMA for the trend state
SLOW_SPAN = 60          # slow EMA for the trend state
TREND_SPAN = 240        # long trend EMA (4 hours)
EXIT_FAST_SPAN = 30     # exit-signal fast EMA
EXIT_SLOW_SPAN = 90     # exit-signal slow EMA
MOM_N = 60              # short momentum lookback (bars)
MOM2_N = 120            # confirming momentum lookback (bars)
FIRST_MOM_BPS = 80      # 60-min return needed for the first entry
MOM_BPS = 50            # 60-min return needed for a later entry
MOM2_BPS = 80           # 120-min return needed for any entry
GAP_BPS = 5             # fast-slow EMA gap must exceed this share of price
TRAIL_BPS = 250         # trailing stop from peak position value
STOP_BPS = 300          # hard stop below entry cost
MIN_HOLD_BARS = 15      # bars before any exit may fire
COOLDOWN_BARS = 30      # bars after an exit before a new entry
ENTRY_CUTOFF = 1200     # no fresh-trend entries after this batch
SOFT_FALLBACK_BAR = 1200  # from here, enter on a mild uptick if never traded
HARD_FALLBACK_BAR = 1380  # from here, buy half size if still never traded
MAX_SLIPPAGE_BPS = 200  # 2%: wide enough for 1-min gaps, still bounds a bad fill


class Ema:
    """EMA seeded as the mean of its first span of closes, then 2/(span+1)
    of the way to each new close. Float64, exactly the baseline convention."""

    def __init__(self, span):
        self.span = span
        self.alpha = 2.0 / (span + 1)
        self.warm = []
        self.value = None

    def push(self, close):
        if self.value is not None:
            self.value += self.alpha * (close - self.value)
            return self.value
        self.warm.append(close)
        if len(self.warm) == self.span:
            self.value = sum(self.warm) / len(self.warm)
        return self.value


class Bot:
    def __init__(self):
        self.fast = Ema(FAST_SPAN)
        self.slow = Ema(SLOW_SPAN)
        self.trend = Ema(TREND_SPAN)
        self.exit_fast = Ema(EXIT_FAST_SPAN)
        self.exit_slow = Ema(EXIT_SLOW_SPAN)
        self.prev_gap = None       # fast - slow after the previous bar
        self.prev_exit_gap = None  # exit_fast - exit_slow after the previous bar
        self.closes_seen = 0
        self.bars_held = 0
        self.bars_since_exit = COOLDOWN_BARS  # start cooled down
        self.peak_value = 0
        self.entry_cost = 0
        self.filled_buys = 0
        self.was_holding = False
        # Close history, plain ints, for the momentum lookbacks.
        self.closes = []

    def on_batch(self, state, batch):
        bars = [b for b in batch["bars"] if b["symbol"] == SYMBOL]
        if not bars:
            return []
        close = int(bars[-1]["c"])

        fast = self.fast.push(close)
        slow = self.slow.push(close)
        trend = self.trend.push(close)
        exit_fast = self.exit_fast.push(close)
        exit_slow = self.exit_slow.push(close)
        self.closes_seen += 1

        if fast is None or slow is None or trend is None:
            return []
        if exit_fast is None or exit_slow is None:
            return []

        gap = fast - slow
        exit_gap = exit_fast - exit_slow
        prev_gap = self.prev_gap
        prev_exit_gap = self.prev_exit_gap
        self.prev_gap = gap
        self.prev_exit_gap = exit_gap
        self.bars_since_exit += 1

        holding = state.positions.get(SYMBOL)
        if holding is not None and not self.was_holding:
            # A buy filled: a new position appeared.
            self.filled_buys += 1
            self.bars_held = 0
            self.peak_value = 0
            self.entry_cost = holding.cost
        self.was_holding = holding is not None

        if holding is not None:
            self.bars_held += 1
            value = state.equity - state.cash
            if value > self.peak_value:
                self.peak_value = value
            if self.bars_held >= MIN_HOLD_BARS and not state.warming_up:
                if exit_gap < 0 and prev_exit_gap is not None and prev_exit_gap >= 0:
                    return [self._sell(state, holding)]
                if self.peak_value > 0 and value < self.peak_value * (1 - TRAIL_BPS / 10000):
                    return [self._sell(state, holding)]
                if self.entry_cost > 0 and value < self.entry_cost * (1 - STOP_BPS / 10000):
                    return [self._sell(state, holding)]
            return []

        # Flat: nothing held. A position that just disappeared starts the cooldown.
        if self.bars_held > 0:
            self.bars_held = 0
            self.bars_since_exit = 0

        if state.warming_up:
            return []
        if self.bars_since_exit < COOLDOWN_BARS:
            return []
        if self.closes_seen <= max(MOM_N, MOM2_N):
            return []

        closes = self.closes
        mom = (close - closes[-1 - MOM_N]) / closes[-1 - MOM_N] * 10000
        mom2 = (close - closes[-1 - MOM2_N]) / closes[-1 - MOM2_N] * 10000
        seq = state.seq

        # Soft fallback: never traded and the window is nearly over.
        if self.filled_buys == 0 and seq >= SOFT_FALLBACK_BAR:
            if close > trend and mom > -50:
                return [self._buy(state, state.cash)]

        # Hard fallback: still never traded in the last hour; buy half size
        # so the entry meets the >=1 trade guardrail whatever the market did.
        if self.filled_buys == 0 and seq >= HARD_FALLBACK_BAR:
            return [self._buy(state, state.cash // 2)]

        if seq >= ENTRY_CUTOFF:
            return []
        if not (gap > 0 and close > trend and gap > close * GAP_BPS / 10000):
            return []
        if mom2 <= MOM2_BPS:
            return []
        if self.filled_buys == 0:
            if mom > FIRST_MOM_BPS:
                return [self._buy(state, state.cash)]
            return []
        # Later entries need a fresh cross, so chop cannot re-trigger them.
        cross_up = (prev_gap is None or prev_gap <= 0) and gap > 0
        if cross_up and mom > MOM_BPS:
            return [self._buy(state, state.cash)]
        return []

    @staticmethod
    def _buy(state, notional):
        room = state.weight_room(SYMBOL)
        amount = min(notional, state.cash, room)
        if amount < int(state.hello["fees"]["min_notional_quote_units"]):
            return None
        return {"side": "buy", "symbol": SYMBOL,
                "notional_quote_units": amount,
                "max_slippage_bps": MAX_SLIPPAGE_BPS}

    @staticmethod
    def _sell(state, holding):
        return {"side": "sell", "symbol": SYMBOL, "qty": holding.qty,
                "max_slippage_bps": MAX_SLIPPAGE_BPS}

    def _record_close(self, close):
        self.closes.append(close)


def main():
    bot = Bot()

    def on_batch(state, batch):
        # Record closes before deciding, so momentum lookbacks see this bar.
        for bar in batch["bars"]:
            if bar["symbol"] == SYMBOL:
                bot._record_close(int(bar["c"]))
        orders = bot.on_batch(state, batch)
        return [o for o in orders if o is not None]

    makefun_bars.run(on_batch)


if __name__ == "__main__":
    main()
