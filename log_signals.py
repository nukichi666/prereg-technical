"""
フェーズB 追跡台帳（prereg_v1.md 第6節の実装）

- イベントの定義は screen_frequency.compute（candidates_v0.2.1）をそのまま使う。新しい判定は加えない
- 将来リターンは計算しない。台帳にはシグナル発生時点で分かる情報だけを書く
- 追記専用：既存の signals/*.csv は書き換えない。再計算した結果と台帳が食い違ったら、
  台帳はそのままにして corrections.csv に理由付きで追記する

記録条件:
  - 足 t の開始時刻が [PHASE_B_START, PHASE_B_END)
  - 足 t と足 t+1（エントリー足）がどちらも確定済み（t+1 の始値時刻が判明していること）
  - 不完全な足（m1_count < 50%）は系列から除外済み（phase_a.py と同じ）

出力:
  signals/YYYY-MM-DD.csv   その実行で新たに台帳へ加えたシグナル（ファイル名は実行日UTC）
  corrections.csv          台帳と再計算の食い違い（自動記録）
  run_log.csv              各実行の記録（入力の最終時刻、コードのハッシュ、ファイルのハッシュ）

使い方:
  python log_signals.py                # 通常運用
  python log_signals.py --data D --root R --start 2026-09-01 --end 2026-10-01   # テスト専用
"""
import argparse
import glob
import hashlib
import os
import subprocess
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from screen_frequency import atr, compute, rsi, trailing_pct, dow_structure

SERIES = [("XAUUSD", "bid"), ("USDJPY", "bid"), ("BTCUSDT", "last"), ("ETHUSDT", "last")]
TFS = {"H1": 60, "H4": 240}
PHASE_B_START = "2026-11-01T00:00:00Z"
PHASE_B_END = "2027-02-01T00:00:00Z"
H = 8
EVENTS = [f"E{i:02d}" for i in range(1, 21)]
DIRECTION = {r: d for r, d in zip(EVENTS, [+1, -1, +1, -1, +1, -1, +1, -1, +1, -1,
                                            +1, -1, +1, -1, +1, -1, +1, +1, +1, -1])}
PARENT = {"E11": "E07", "E12": "E08", "E13": "E09", "E14": "E10",
          "E15": "E05", "E16": "E06", "E17": "E01", "E18": "E01"}
CODE_FILES = ["log_signals.py", "screen_frequency.py", "resample.py"]
COLS = ["signal_id", "rule_id", "asset", "timeframe", "price_type", "direction",
        "signal_bar_open_utc", "signal_bar_close_utc", "entry_time_utc",
        "evaluation_due_utc_est", "parent_signal_id",
        "close", "close_prev", "rsi14", "rsi14_prev", "bb_lower", "bb_upper",
        "bb_lower_prev", "bb_upper_prev", "atr14", "atr_pct", "bbw_pct_prev",
        "dow_up", "dow_down", "status", "input_last_bar_utc", "code_sha256", "git_sha",
        "logged_at_utc", "row_sha256"]


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def code_hash(root):
    h = hashlib.sha256()
    for f in CODE_FILES:
        p = os.path.join(root, f)
        if os.path.exists(p):
            h.update(f.encode() + b"\0" + open(p, "rb").read())
    return h.hexdigest()


def git_sha(root):
    try:
        return subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip() or ""
    except Exception:
        return ""


def sid(rule, asset, tf, ts):
    return f"{rule}-{asset}-{tf}-{ts.strftime('%Y%m%dT%H%MZ')}"


def iso(ts):
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def load(data_dir, asset, tf, pt):
    df = pd.read_csv(os.path.join(data_dir, f"{asset}_{tf}_{pt}.csv"), parse_dates=["time_utc"])
    return df[df["m1_count"] >= TFS[tf] * 0.5].set_index("time_utc").sort_index()


def recompute(data_dir, start, end):
    """フェーズB期間の全イベントを再計算して返す（記録条件を満たすものだけ）。"""
    out = []
    last_bar = {}
    for asset, pt in SERIES:
        for tf, mins in TFS.items():
            df = load(data_dir, asset, tf, pt)
            ev, _ = compute(df)
            c = df["close"]
            r = rsi(c)
            mid = c.rolling(20).mean()
            sd = c.rolling(20).std(ddof=0)
            lo, hi = mid - 2 * sd, mid + 2 * sd
            bbw_p = trailing_pct((hi - lo) / mid)
            atr14 = atr(df)
            atr_p = trailing_pct(atr14 / c)
            dup, ddn = dow_structure(df)
            idx = df.index
            n = len(df)
            last_bar[(asset, tf)] = idx[-1]
            step = pd.Timedelta(minutes=mins)
            for t in range(1, n - 1):          # t+1 が存在するものだけ
                ts = idx[t]
                if not (start <= ts < end):
                    continue
                for rule in EVENTS:
                    if not ev[rule].iloc[t]:
                        continue
                    par = PARENT.get(rule)
                    out.append({
                        "signal_id": sid(rule, asset, tf, ts), "rule_id": rule, "asset": asset,
                        "timeframe": tf, "price_type": pt, "direction": DIRECTION[rule],
                        "signal_bar_open_utc": iso(ts), "signal_bar_close_utc": iso(ts + step),
                        "entry_time_utc": iso(idx[t + 1]),
                        "evaluation_due_utc_est": iso(idx[t + H] + step) if t + H < n
                        else iso(ts + step * (H + 1)),
                        "parent_signal_id": sid(par, asset, tf, ts) if par else "",
                        "close": c.iloc[t], "close_prev": c.iloc[t - 1],
                        "rsi14": r.iloc[t], "rsi14_prev": r.iloc[t - 1],
                        "bb_lower": lo.iloc[t], "bb_upper": hi.iloc[t],
                        "bb_lower_prev": lo.iloc[t - 1], "bb_upper_prev": hi.iloc[t - 1],
                        "atr14": atr14.iloc[t], "atr_pct": atr_p.iloc[t],
                        "bbw_pct_prev": bbw_p.iloc[t - 1],
                        "dow_up": bool(dup.iloc[t]), "dow_down": bool(ddn.iloc[t]),
                        "series_last_bar": idx[-1],
                    })
    return pd.DataFrame(out), last_bar


def read_ledger(root):
    files = sorted(glob.glob(os.path.join(root, "signals", "*.csv")))
    if not files:
        return pd.DataFrame(columns=COLS)
    return pd.concat([pd.read_csv(f, dtype=str, keep_default_na=False) for f in files], ignore_index=True)


def canon(v):
    """CSVに書く文字列と同じ表記に正規化する（ハッシュと書き込みの両方に使う）。"""
    if isinstance(v, (bool, np.bool_)):
        return "True" if v else "False"
    if isinstance(v, (float, np.floating)):
        return "" if not np.isfinite(v) else f"{float(v):.10g}"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    return "" if v is None else str(v)


def row_hash(row):
    return sha256_bytes("|".join(str(row[c]) for c in COLS if c != "row_sha256").encode())


def append_csv(path, df):
    new = not os.path.exists(path)
    df.to_csv(path, mode="a", header=new, index=False, lineterminator="\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--root", default=".")
    ap.add_argument("--start", default=PHASE_B_START)
    ap.add_argument("--end", default=PHASE_B_END)
    ap.add_argument("--now", default=None, help="テスト用：実行時刻(UTC)を固定")
    args = ap.parse_args()
    def utc(x):
        t = pd.Timestamp(x)
        return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")

    start, end = utc(args.start), utc(args.end)
    now = utc(args.now) if args.now else pd.Timestamp(datetime.now(timezone.utc))
    os.makedirs(os.path.join(args.root, "signals"), exist_ok=True)

    calc, last_bar = recompute(args.data, start, end)
    ledger = read_ledger(args.root)
    have = set(ledger["signal_id"]) if len(ledger) else set()
    chash = code_hash(args.root)
    gsha = git_sha(args.root)

    # 系列ごとの記録済み範囲（台帳にある最新の signal_bar_open）
    wm = {}
    if len(ledger):
        lg = ledger.assign(_t=pd.to_datetime(ledger["signal_bar_open_utc"], utc=True))
        wm = lg.groupby(["asset", "timeframe"])["_t"].max().to_dict()

    new_rows, corr = [], []
    calc_ids = set()
    for _, r in calc.iterrows():
        calc_ids.add(r["signal_id"])
        if r["signal_id"] in have:
            continue
        ts = pd.Timestamp(r["signal_bar_open_utc"])
        w = wm.get((r["asset"], r["timeframe"]))
        row = {k: r[k] for k in COLS if k in r}
        row.update({"status": "pending", "input_last_bar_utc": iso(r["series_last_bar"]),
                    "code_sha256": chash, "git_sha": gsha, "logged_at_utc": iso(now)})
        row = {k: canon(row.get(k, "")) for k in COLS}
        row["row_sha256"] = row_hash(row)
        new_rows.append(row)
        if w is not None and ts <= w:   # 記録済み範囲より前に新しく現れた
            corr.append({"logged_at_utc": iso(now), "signal_id": r["signal_id"], "type": "late_appearance",
                         "detail": "既に記録済みの範囲内で、再計算により新たに検出された（データ修正の可能性）"})
    for s in sorted(have - calc_ids):
        corr.append({"logged_at_utc": iso(now), "signal_id": s, "type": "vanished",
                     "detail": "台帳にあるが、現在のデータで再計算すると検出されない（データ修正の可能性）"})

    day_file = os.path.join(args.root, "signals", f"{now.strftime('%Y-%m-%d')}.csv")
    if new_rows:
        nr = pd.DataFrame(new_rows, columns=COLS).sort_values(["signal_bar_open_utc", "asset", "timeframe", "rule_id"])
        append_csv(day_file, nr)
    if corr:
        known = set()
        cp = os.path.join(args.root, "corrections.csv")
        if os.path.exists(cp):
            old = pd.read_csv(cp, dtype=str, keep_default_na=False)
            known = set(zip(old["signal_id"], old["type"]))
        corr = [c for c in corr if (c["signal_id"], c["type"]) not in known]  # 同じ食い違いは1回だけ記録
        if corr:
            append_csv(cp, pd.DataFrame(corr))

    fh = sha256_bytes(open(day_file, "rb").read()) if os.path.exists(day_file) else ""
    lb = ";".join(f"{a}_{t}={iso(v)}" for (a, t), v in sorted(last_bar.items()))
    append_csv(os.path.join(args.root, "run_log.csv"), pd.DataFrame([{
        "run_utc": iso(now), "new_signals": len(new_rows), "corrections": len(corr),
        "day_file": os.path.basename(day_file) if new_rows else "", "day_file_sha256": fh,
        "code_sha256": chash, "git_sha": gsha, "input_last_bars": lb}]))
    print(f"[{iso(now)}] 新規シグナル {len(new_rows)} 件, 食い違い {len(corr)} 件, 台帳合計 {len(have) + len(new_rows)} 件")


if __name__ == "__main__":
    main()
