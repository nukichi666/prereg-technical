"""
M1 -> H1 / H4 再集計スクリプト（事前登録研究用）

定義（PREREGに転記する）:
  - 時刻基準: UTC
  - H1: UTC毎正時 [hh:00, hh+1:00) 、ラベルは区間の開始時刻
  - H4: UTC 00/04/08/12/16/20時起点 [h, h+4) 、ラベルは区間の開始時刻
  - OHLC: 区間内M1の first/max/min/last
  - m1_count: 区間内のM1本数（欠損判定用。除外ルールはPREREGで固定する）
  - M1が1本もない区間は出力しない（週末・休場）
  - Dukascopyの埋め草M1を除外: O=H=L=C かつ 直前M1の終値と同値 のM1が
    15本以上連続する区間は、その連続部分を全て削除する（休場中のフラット補完）

入力:
  raw/dukascopy/*.csv  (dukascopy-node出力。ファイル名: 銘柄-m1-bid|ask-開始-終了.csv)
  raw/binance/*.csv    (data.binance.visionの月次1分足。ヘッダなし)
出力:
  data/{ASSET}_{H1|H4}_{bid|ask|last}.csv
"""
import glob
import os
import sys

import pandas as pd

RAW_DUKA = os.path.join("raw", "dukascopy")
RAW_BINANCE = os.path.join("raw", "binance")
OUT = "data"


def to_utc(ts: pd.Series) -> pd.DatetimeIndex:
    """エポック値をUTCに変換。ms / µs を自動判別（Binance現物は2025年以降µs）。"""
    ts = pd.to_numeric(ts, errors="coerce")
    unit = pd.Series("ms", index=ts.index)
    unit[ts > 1e14] = "us"
    out = pd.Series(pd.NaT, index=ts.index, dtype="datetime64[ns, UTC]")
    for u in ("ms", "us"):
        m = unit == u
        if m.any():
            out[m] = pd.to_datetime(ts[m].astype("int64"), unit=u, utc=True)
    out = pd.DatetimeIndex(out)
    if out.min() < pd.Timestamp("2000-01-01", tz="UTC"):
        sys.exit(f"時刻の単位がおかしい可能性: 最小値 {out.min()}（秒単位のデータが混ざっていないか確認）")
    return out


FILLER_MIN_RUN = 15


def drop_filler(df: pd.DataFrame) -> pd.DataFrame:
    """休場中のフラット補完足（同値フラットが FILLER_MIN_RUN 本以上連続）を除去。"""
    df = df[~df.index.duplicated(keep="first")].sort_index()
    flat = (df["open"] == df["high"]) & (df["high"] == df["low"]) & (df["low"] == df["close"])
    same = flat & (df["close"] == df["close"].shift(1))
    grp = (same != same.shift()).cumsum()
    run = same.groupby(grp).transform("size").where(same, 0)
    return df[run < FILLER_MIN_RUN]


def load_dukascopy():
    """{(asset, price_type): DataFrame(index=UTC, OHLC)}"""
    res = {}
    for path in sorted(glob.glob(os.path.join(RAW_DUKA, "*.csv"))):
        name = os.path.basename(path).lower()
        parts = name.split("-")
        if len(parts) < 3 or parts[1] != "m1":
            print(f"[skip] 想定外のファイル名: {name}")
            continue
        asset, ptype = parts[0].upper(), parts[2]
        if os.path.getsize(path) == 0:
            continue  # 休場日は空ファイルになる
        df = pd.read_csv(path)
        if df.empty:
            continue
        df.columns = [c.lower() for c in df.columns]
        df.index = to_utc(df["timestamp"])
        df = df[["open", "high", "low", "close"]].astype(float)
        key = (asset, ptype)
        res[key] = pd.concat([res[key], df]) if key in res else df
    for key in res:
        before = len(res[key])
        res[key] = drop_filler(res[key])
        print(f"[filler] {key[0]} {key[1]}: {before - len(res[key])} 本のM1を除外")
    return res


def load_binance():
    res = {}
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "trades", "taker_base", "taker_quote", "ignore"]
    for path in sorted(glob.glob(os.path.join(RAW_BINANCE, "*.csv"))):
        asset = os.path.basename(path).split("-")[0].upper()  # BTCUSDT
        if os.path.getsize(path) == 0:
            continue
        df = pd.read_csv(path, header=None, names=cols)
        df = df[pd.to_numeric(df["open_time"], errors="coerce").notna()]  # ヘッダ行対策
        df.index = to_utc(df["open_time"])
        df = df[["open", "high", "low", "close"]].astype(float)
        key = (asset, "last")
        res[key] = pd.concat([res[key], df]) if key in res else df
    return res


def resample(m1: pd.DataFrame, rule: str) -> pd.DataFrame:
    m1 = m1[~m1.index.duplicated(keep="first")].sort_index()
    g = m1.resample(rule, label="left", closed="left", origin="epoch")
    out = pd.DataFrame({
        "open": g["open"].first(),
        "high": g["high"].max(),
        "low": g["low"].min(),
        "close": g["close"].last(),
        "m1_count": g["close"].count(),
    })
    return out[out["m1_count"] > 0]


def main():
    os.makedirs(OUT, exist_ok=True)
    series = {**load_dukascopy(), **load_binance()}
    if not series:
        sys.exit("raw/ 以下にCSVが見つかりません。先にデータ取得を実行してください。")

    print(f"{'asset':10}{'type':6}{'tf':4}{'bars':>8}  {'from':25}{'to':25}{'thin(<50%)':>11}")
    for (asset, ptype), m1 in sorted(series.items()):
        for tf, rule, full in (("H1", "1h", 60), ("H4", "4h", 240)):
            bars = resample(m1, rule)
            thin = int((bars["m1_count"] < full * 0.5).sum())
            bars.index.name = "time_utc"
            bars.to_csv(os.path.join(OUT, f"{asset}_{tf}_{ptype}.csv"))
            print(f"{asset:10}{ptype:6}{tf:4}{len(bars):>8}  "
                  f"{str(bars.index.min()):25}{str(bars.index.max()):25}{thin:>11}")


if __name__ == "__main__":
    main()
