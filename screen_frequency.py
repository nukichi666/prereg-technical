"""
頻度スクリーナー（candidates_v0.2.1 第4節の実装）

このスクリプトは「イベントの発生件数」と「フィルターが真の足の割合」だけを数える。
将来リターン・勝率・PF・MFE/MAE・p値など成績に関わる量は一切計算しない
（評価窓 H は非重複件数の判定にのみ使い、価格は参照しない）。

入力: data/{ASSET}_{H1|H4}_{bid|last}.csv  (resample.py の出力)
出力: frequency_v0.2.1.csv, filters_v0.2.1.csv, 標準出力に集計
"""
import os

import numpy as np
import pandas as pd

DATA = "data"
SERIES = [("XAUUSD", "bid"), ("USDJPY", "bid"), ("BTCUSDT", "last"), ("ETHUSDT", "last")]
TFS = {"H1": 60, "H4": 240}
START = pd.Timestamp("2025-10-01T00:00:00Z")
END = pd.Timestamp("2026-10-01T00:00:00Z")   # 半開区間 [START, END)
WARMUP = 500
PCT_WIN = 500
HS = (8, 4, 16)   # 8 が主評価期間
EVENTS = [f"E{i:02d}" for i in range(1, 21)]
FILTERS = [f"F{i:02d}" for i in range(1, 8)]


# ---------- 指標（第0節の定義） ----------
def wilder(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up, dn = wilder(d.clip(lower=0), n), wilder((-d).clip(lower=0), n)
    return 100 - 100 / (1 + up / dn)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return wilder(tr, n)


def trailing_pct(x: pd.Series, win: int = PCT_WIN) -> pd.Series:
    """当該足を含む直近 win 本の中で、当該値以下の割合（%）。"""
    v = x.to_numpy()
    out = np.full(len(v), np.nan)
    if len(v) >= win:
        w = np.lib.stride_tricks.sliding_window_view(v, win)
        cur = w[:, -1:]
        ok = ~np.isnan(w).any(axis=1)
        out[win - 1:] = np.where(ok, (w <= cur).sum(axis=1) / win * 100, np.nan)
    return pd.Series(out, index=x.index)


def dow_structure(df: pd.DataFrame, k: int = 3):
    """スイング高安（前後k本）を s+k で確定させ、確定済みの直近2点で上昇/下降ダウを判定。"""
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    n = len(df)
    up = np.zeros(n, bool)
    down = np.zeros(n, bool)
    highs, lows = [], []
    for t in range(n):
        s = t - k                      # 足 t の確定時に確定するスイング候補
        if s - k >= 0:
            if h[s] == h[s - k:s + k + 1].max():
                highs.append(h[s])
            if l[s] == l[s - k:s + k + 1].min():
                lows.append(l[s])
        if len(highs) >= 2 and len(lows) >= 2:
            up[t] = highs[-1] > highs[-2] and lows[-1] > lows[-2]
            down[t] = highs[-1] < highs[-2] and lows[-1] < lows[-2]
    return pd.Series(up, df.index), pd.Series(down, df.index)


# ---------- イベント（第1節・1a節） ----------
def edge(c: pd.Series) -> pd.Series:
    c = c.fillna(False).astype(bool)
    return c & ~c.shift(1, fill_value=False)


def compute(df: pd.DataFrame):
    c = df["close"]
    r = rsi(c)
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std(ddof=0)
    lo, hi = mid - 2 * sd, mid + 2 * sd
    bbw_p = trailing_pct((hi - lo) / mid)
    atr_p = trailing_pct(atr(df) / c)
    dup, ddn = dow_structure(df)

    ev = {}
    # 非入れ子：条件全体の偽→真
    ev["E01"] = edge(r < 30); ev["E02"] = edge(r > 70)
    ev["E03"] = edge(r < 25); ev["E04"] = edge(r > 75)
    ev["E05"] = edge(r < 35); ev["E06"] = edge(r > 65)
    ev["E07"] = edge(c < lo); ev["E08"] = edge(c > hi)
    ev["E09"] = edge((c.shift(1) < lo.shift(1)) & (c >= lo))
    ev["E10"] = edge((c.shift(1) > hi.shift(1)) & (c <= hi))
    ev["E19"] = edge((bbw_p.shift(1) < 20) & (c > hi))
    ev["E20"] = edge((bbw_p.shift(1) < 20) & (c < lo))
    # 入れ子：親イベントの発火足で追加条件を評価
    ev["E11"] = ev["E07"] & (r < 30)
    ev["E12"] = ev["E08"] & (r > 70)
    ev["E13"] = ev["E09"] & (r.shift(1) < 30)
    ev["E14"] = ev["E10"] & (r.shift(1) > 70)
    ev["E15"] = ev["E05"] & dup
    ev["E16"] = ev["E06"] & ddn
    ev["E17"] = ev["E01"] & (atr_p > 70)
    ev["E18"] = ev["E01"] & (atr_p < 30)

    ema20, ema50 = c.ewm(span=20, adjust=False).mean(), c.ewm(span=50, adjust=False).mean()
    fl = {"F01": ema20 > ema50, "F02": ema20 < ema50, "F03": dup, "F04": ddn,
          "F05": atr_p > 70, "F06": atr_p < 30, "F07": bbw_p < 20}
    return ev, fl


def non_overlapping(pos: np.ndarray, H: int) -> int:
    n, last = 0, None
    for p in pos:
        if last is None or p - last >= H:
            n += 1
            last = p
    return n


def main():
    rows, frows = [], []
    for asset, pt in SERIES:
        for tf, full in TFS.items():
            df = pd.read_csv(os.path.join(DATA, f"{asset}_{tf}_{pt}.csv"), parse_dates=["time_utc"])
            df = df[df["m1_count"] >= full * 0.5].set_index("time_utc").sort_index()  # 不完全な足は除外
            ev, fl = compute(df)
            pos = np.arange(len(df))
            inper = (df.index >= START) & (df.index < END) & (pos >= WARMUP)
            for rid in EVENTS:
                e = ev[rid].to_numpy() & inper
                p = pos[e]
                row = {"rule_id": rid, "asset": asset, "timeframe": tf, "events_12m": int(e.sum())}
                for H in HS:
                    row[f"non_overlapping_events_12m_H{H}"] = non_overlapping(p, H)
                rows.append(row)
            for fid in FILTERS:
                v = fl[fid].fillna(False).to_numpy()[inper]
                frows.append({"filter_id": fid, "asset": asset, "timeframe": tf,
                              "bars_in_period": int(inper.sum()), "share_true": round(float(v.mean()), 4)})

    out = pd.DataFrame(rows)
    out["events_per_month"] = (out["events_12m"] / 12).round(2)
    out["expected_events_3m"] = (out["events_12m"] / 4).round(2)
    out["expected_non_overlapping_3m_H8"] = (out["non_overlapping_events_12m_H8"] / 4).round(2)
    cols = ["rule_id", "asset", "timeframe", "events_12m", "non_overlapping_events_12m_H8",
            "non_overlapping_events_12m_H4", "non_overlapping_events_12m_H16", "events_per_month",
            "expected_events_3m", "expected_non_overlapping_3m_H8"]
    out[cols].to_csv("frequency_v0.2.1.csv", index=False)
    pd.DataFrame(frows).to_csv("filters_v0.2.1.csv", index=False)

    agg = out.groupby("rule_id")[["events_12m", "non_overlapping_events_12m_H8"]].sum()
    agg["expected_non_overlapping_3m_H8"] = agg["non_overlapping_events_12m_H8"] / 4
    agg["pass(>=30)"] = agg["expected_non_overlapping_3m_H8"] >= 30
    print(agg.to_string())


if __name__ == "__main__":
    main()
