"""
フェーズA 解析（prereg_v1.md の実装）

- 入力: data/ 以下の H1/H4 CSV（resample.py の出力）。実行前に data_manifest.csv の SHA-256 と照合し、
  1つでも一致しなければ停止する。
- イベント検出は screen_frequency.compute（candidates_v0.2.1 の定義）をそのまま使う。
- 出力（--out ディレクトリ）:
    primary.csv     主要3対比（H=8, グロス）と Holm 補正
    secondary.csv   感度分析（H=4/16、コスト控除、勝率差）— 推論的結論は出さない
    events.csv      全イベントのアウトカム（再現・監査用）
    run_info.txt    実行環境・入力ハッシュ・乱数シード

使い方:
    python phase_a.py --data data --out results/phase_a
    python phase_a.py --data <dir> --out <dir> --skip-manifest   # ダミーデータでのテスト専用
"""
import argparse
import hashlib
import os
import platform
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from screen_frequency import atr, compute

SERIES = [("XAUUSD", "bid"), ("USDJPY", "bid"), ("BTCUSDT", "last"), ("ETHUSDT", "last")]
FX = {"XAUUSD", "USDJPY"}
TFS = {"H1": 60, "H4": 240}
START = pd.Timestamp("2024-04-01T00:00:00Z")
END = pd.Timestamp("2026-10-01T00:00:00Z")   # 半開区間 [START, END)
WARMUP = 500
H_MAIN, H_SENS = 8, (4, 16)
CRYPTO_RT_COST = 0.0010
B = 10_000
SEED = 20261101
ALPHA = 0.05

PRIMARY = [  # (id, child, parent, direction)
    ("P2-L", "E13", "E09", +1),
    ("P2-U", "E14", "E10", -1),
    ("P3-HV", "E17", "E01", +1),
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_manifest(data_dir, manifest="data_manifest.csv"):
    m = pd.read_csv(manifest)
    bad = []
    for _, r in m.iterrows():
        p = os.path.join(data_dir, r["filename"])
        if not os.path.exists(p) or sha256(p) != r["sha256"]:
            bad.append(r["filename"])
    if bad:
        sys.exit(f"入力データがマニフェストと一致しません: {bad}")
    return m


def load(data_dir, asset, tf, pt):
    df = pd.read_csv(os.path.join(data_dir, f"{asset}_{tf}_{pt}.csv"), parse_dates=["time_utc"])
    return df[df["m1_count"] >= TFS[tf] * 0.5].set_index("time_utc").sort_index()


def event_outcomes(data_dir):
    """全系列・全主要対比の親イベントについて、アウトカムを1行ずつ作る。"""
    rows = []
    for asset, pt in SERIES:
        for tf in TFS:
            df = load(data_dir, asset, tf, pt)
            ask_open = None
            if asset in FX:
                ask_open = load(data_dir, asset, tf, "ask")["open"]
            ev, _ = compute(df)
            a = atr(df).to_numpy()
            o, c = df["open"].to_numpy(), df["close"].to_numpy()
            idx = df.index
            n = len(df)
            pos = np.arange(n)
            inper = (idx >= START) & (idx < END) & (pos >= WARMUP)
            for pid, child, parent, d in PRIMARY:
                par = ev[parent].to_numpy() & inper
                chi = ev[child].to_numpy()
                for t in pos[par]:
                    if t + 1 >= n or np.isnan(a[t]):
                        continue
                    row = {"time_utc": idx[t], "asset": asset, "timeframe": tf, "contrast": pid,
                           "is_child": bool(chi[t]), "direction": d}
                    scale = a[t] / c[t]
                    # コスト（片道スプレッド×2 または 暗号資産は往復0.10%）
                    if asset in FX:
                        ts1 = idx[t + 1]
                        sp = ask_open.get(ts1, np.nan) - o[t + 1]
                        cost = 2 * sp / o[t + 1] if np.isfinite(sp) else np.nan
                    else:
                        cost = CRYPTO_RT_COST
                    row["cost_rt"] = cost
                    for H in (H_MAIN,) + H_SENS:
                        if t + H < n:
                            lr = d * np.log(c[t + H] / o[t + 1])
                            row[f"y_H{H}"] = lr / scale
                            row[f"ycost_H{H}"] = (lr - cost) / scale if np.isfinite(cost) else np.nan
                        else:
                            row[f"y_H{H}"] = np.nan
                            row[f"ycost_H{H}"] = np.nan
                    rows.append(row)
    ev = pd.DataFrame(rows)
    wk = ev["time_utc"].dt.tz_convert("UTC").dt.tz_localize(None).dt.to_period("W-SUN")
    ev["week"] = wk.dt.start_time  # 月曜00:00起点のUTC暦週
    return ev


def bootstrap_counts(weeks):
    """全対比・全指標で共通の週リサンプリング（B×週数のカウント行列）。"""
    rng = np.random.default_rng(SEED)
    k = len(weeks)
    draws = rng.integers(0, k, size=(B, k))
    cnt = np.zeros((B, k), dtype=np.int32)
    for b in range(B):
        cnt[b] = np.bincount(draws[b], minlength=k)
    return cnt


def contrast(sub, col, weeks, cnt, transform=None):
    """Δ = mean(C) − mean(P\\C) と、そのブートストラップ分布。"""
    s = sub[np.isfinite(sub[col])].copy()
    v = s[col].to_numpy()
    if transform is not None:
        v = transform(v)
    s["_v"] = v
    widx = {w: i for i, w in enumerate(weeks)}
    s["_w"] = s["week"].map(widx).to_numpy()
    k = len(weeks)

    def agg(mask):
        g = s[mask]
        sums = np.bincount(g["_w"], weights=g["_v"], minlength=k)
        ns = np.bincount(g["_w"], minlength=k).astype(float)
        return sums, ns

    cs, cn = agg(s["is_child"].to_numpy())
    rs, rn = agg(~s["is_child"].to_numpy())
    est = cs.sum() / cn.sum() - rs.sum() / rn.sum()
    with np.errstate(invalid="ignore", divide="ignore"):
        bc = (cnt @ cs) / (cnt @ cn) - (cnt @ rs) / (cnt @ rn)
    ok = np.isfinite(bc)
    bc_ok = bc[ok]
    p = (1 + np.sum(bc_ok <= 0)) / (len(bc_ok) + 1)
    lo, hi = np.percentile(bc_ok, [2.5, 97.5])
    return {"n_child": int(cn.sum()), "n_rest": int(rn.sum()),
            "mean_child": cs.sum() / cn.sum(), "mean_rest": rs.sum() / rn.sum(),
            "delta": est, "ci95_lo": lo, "ci95_hi": hi, "p_one_sided": p,
            "bootstrap_nan": int((~ok).sum())}


def holm(ps, alpha=ALPHA):
    order = np.argsort(ps)
    m = len(ps)
    adj = np.empty(m)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * ps[i]))
        adj[i] = running
    return adj


def verdict(p_raw, p_holm):
    if p_holm < ALPHA:
        return "事前登録した基準で支持された"
    if p_raw < ALPHA:
        return "示唆はあるが、事前登録した基準では支持されなかった"
    return "支持されなかった"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default=os.path.join("results", "phase_a"))
    ap.add_argument("--skip-manifest", action="store_true")
    args = ap.parse_args()

    manifest = None if args.skip_manifest else verify_manifest(args.data)
    os.makedirs(args.out, exist_ok=True)

    ev = event_outcomes(args.data)
    weeks = pd.date_range(START.tz_localize(None).to_period("W-SUN").start_time,
                          (END - pd.Timedelta(seconds=1)).tz_localize(None).to_period("W-SUN").start_time,
                          freq="7D")
    cnt = bootstrap_counts(list(weeks))

    prim = []
    for pid, child, parent, d in PRIMARY:
        r = contrast(ev[ev["contrast"] == pid], f"y_H{H_MAIN}", list(weeks), cnt)
        prim.append({"contrast": pid, "child": child, "parent": parent, "H": H_MAIN, **r})
    prim = pd.DataFrame(prim)
    prim["p_holm"] = holm(prim["p_one_sided"].to_numpy())
    prim["verdict"] = [verdict(a, b) for a, b in zip(prim["p_one_sided"], prim["p_holm"])]
    prim.to_csv(os.path.join(args.out, "primary.csv"), index=False)

    sec = []
    for pid, child, parent, d in PRIMARY:
        sub = ev[ev["contrast"] == pid]
        for H in H_SENS:
            sec.append({"contrast": pid, "analysis": f"gross_H{H}",
                        **contrast(sub, f"y_H{H}", list(weeks), cnt)})
        for H in (H_MAIN,) + H_SENS:
            sec.append({"contrast": pid, "analysis": f"cost_H{H}",
                        **contrast(sub, f"ycost_H{H}", list(weeks), cnt)})
        sec.append({"contrast": pid, "analysis": f"winrate_H{H_MAIN}",
                    **contrast(sub, f"y_H{H_MAIN}", list(weeks), cnt, transform=lambda v: (v > 0).astype(float))})
    sec = pd.DataFrame(sec)
    sec = sec.drop(columns=["p_one_sided"])  # 感度分析では有意性を主張しない
    sec.to_csv(os.path.join(args.out, "secondary.csv"), index=False)

    ev.drop(columns=["week"]).to_csv(os.path.join(args.out, "events.csv"), index=False)

    with open(os.path.join(args.out, "run_info.txt"), "w", encoding="utf-8") as f:
        f.write(f"run_utc: {datetime.now(timezone.utc).isoformat()}\n")
        f.write(f"python: {sys.version.split()[0]}  numpy: {np.__version__}  pandas: {pd.__version__}\n")
        f.write(f"platform: {platform.platform()}\n")
        f.write(f"period: [{START.isoformat()}, {END.isoformat()})  H_main={H_MAIN}  B={B}  seed={SEED}\n")
        f.write(f"weeks_in_bootstrap: {len(weeks)}\n")
        f.write(f"manifest_verified: {manifest is not None}\n")
        if manifest is not None:
            for _, r in manifest.iterrows():
                f.write(f"  {r['filename']}  {r['sha256']}\n")

    pd.set_option("display.width", 200)
    print(prim[["contrast", "n_child", "n_rest", "mean_child", "mean_rest", "delta",
                "ci95_lo", "ci95_hi", "p_one_sided", "p_holm", "verdict"]].to_string(index=False))


if __name__ == "__main__":
    main()
