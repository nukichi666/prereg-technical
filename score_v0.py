"""
スコア仕様 v0（score_spec_v0.md）の実装

- フェーズA（参考適用）:
    python score_v0.py --data data
  1) phase_a.event_outcomes でイベントを再計算し、results/phase_a/events.csv と照合（不一致なら停止）
  2) 同期間・資産・時間足・方向の全足エントリー平均（ベースライン b、グロス）を計算
  3) 超過期待値 E_x = mean(ycost_H8 − b) を週ブロック・ブートストラップし、Edge Score に変換
  出力: results/score_v0/phase_a_scores.csv, phase_a_baseline.csv, phase_a_scorecard.md

- フェーズB（劣化チェック。2027-02-08 UTC より前は実行を拒否）:
    python score_v0.py --data data --phase B

- テスト専用（ダミーデータ）:
    python score_v0.py --data <dir> --events <dummy events.csv> --out <dir> --skip-verify
"""
import argparse
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import phase_a
from phase_a import B, SERIES, TFS, WARMUP, atr, load

H = 8
SEED_A, SEED_B = 20261101, 20261102
PHASE_A = (pd.Timestamp("2024-04-01T00:00:00Z"), pd.Timestamp("2026-10-01T00:00:00Z"))
PHASE_B = (pd.Timestamp("2026-11-01T00:00:00Z"), pd.Timestamp("2027-02-01T00:00:00Z"))
PHASE_B_EARLIEST_RUN = pd.Timestamp("2027-02-08T00:00:00Z")

# 第5節 固定アンカー（E_x[R] → 点）
ANCHOR_X = [0.00, 0.05, 0.10, 0.20, 0.30]
ANCHOR_Y = [0, 25, 50, 75, 100]

# 第1節 対象イベント群: (群, events.csv の対比, 子だけか, 方向, 主要対比に含まれるか)
SETS = [
    ("E09", "P2-L", False, +1, True),
    ("E13", "P2-L", True, +1, True),
    ("E10", "P2-U", False, -1, True),
    ("E14", "P2-U", True, -1, True),
    ("E01", "P3-HV", False, +1, True),
    ("E17", "P3-HV", True, +1, True),
]
LABEL = {
    "E09": "BB下限の外→内へ復帰（買い）",
    "E13": "E09 ＋ 直前RSI<30（買い）",
    "E10": "BB上限の外→内へ復帰（売り）",
    "E14": "E10 ＋ 直前RSI>70（売り）",
    "E01": "RSI<30（買い）",
    "E17": "E01 ＋ 高ボラ（買い）",
}
AUTOMATION = "Full"  # 第9節：v0 の対象はすべてコードで一意に判定


def edge_score(x):
    return np.interp(x, ANCHOR_X, ANCHOR_Y)


def weeks_of(start, end):
    s = start.tz_localize(None).to_period("W-SUN").start_time
    e = (end - pd.Timedelta(seconds=1)).tz_localize(None).to_period("W-SUN").start_time
    return list(pd.date_range(s, e, freq="7D"))


def week_col(times):
    t = pd.to_datetime(times, utc=True)
    return t.dt.tz_localize(None).dt.to_period("W-SUN").dt.start_time


def boot_counts(k, seed):
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, k, size=(B, k))
    cnt = np.zeros((B, k), dtype=np.int32)
    for b in range(B):
        cnt[b] = np.bincount(draws[b], minlength=k)
    return cnt


def event_outcomes_for(data_dir, start, end):
    """phase_a.event_outcomes を期間だけ差し替えて呼ぶ（定義は phase_a.py のまま）。"""
    old = (phase_a.START, phase_a.END)
    phase_a.START, phase_a.END = start, end
    try:
        return phase_a.event_outcomes(data_dir)
    finally:
        phase_a.START, phase_a.END = old


def verify_against_published(ev, published_csv):
    """再計算したイベントが公開済み events.csv と一致するか。"""
    pub = pd.read_csv(published_csv)
    key = ["time_utc", "asset", "timeframe", "contrast", "is_child"]
    a = ev.copy()
    a["time_utc"] = pd.to_datetime(a["time_utc"], utc=True)
    pub["time_utc"] = pd.to_datetime(pub["time_utc"], utc=True)
    if len(a) != len(pub):
        sys.exit(f"イベント数が公開済み events.csv と一致しません: 再計算 {len(a)} / 公開 {len(pub)}")
    m = a.merge(pub, on=key, how="outer", suffixes=("_new", "_pub"), indicator=True)
    if (m["_merge"] != "both").any():
        sys.exit(f"イベントの集合が一致しません（{int((m['_merge'] != 'both').sum())} 件）")
    for col in ("y_H8", "ycost_H8"):
        x, y = m[f"{col}_new"].to_numpy(float), m[f"{col}_pub"].to_numpy(float)
        same = (np.isnan(x) & np.isnan(y)) | np.isclose(x, y, rtol=0, atol=1e-9)
        if not same.all():
            sys.exit(f"{col} が公開済み events.csv と {int((~same).sum())} 件食い違います")
    print(f"[verify] 公開済み events.csv と一致（{len(a)} 件）")


def baseline(data_dir, start, end):
    """第3節：期間内の全足からエントリーした場合のグロス平均（買い方向）。売りは符号反転。"""
    rows = []
    for asset, pt in SERIES:
        for tf in TFS:
            df = load(data_dir, asset, tf, pt)
            a = atr(df).to_numpy()
            o, c = df["open"].to_numpy(), df["close"].to_numpy()
            idx = df.index
            n = len(df)
            t = np.arange(n)
            ok = (idx >= start) & (idx < end) & (t >= WARMUP) & (t + H < n) & np.isfinite(a)
            t = t[ok]
            y = np.log(c[t + H] / o[t + 1]) / (a[t] / c[t])
            for d in (+1, -1):
                rows.append({"asset": asset, "timeframe": tf, "direction": d,
                             "n_bars": int(len(y)), "b": float(d * y.mean())})
    return pd.DataFrame(rows)


def score_set(sub, weeks, cnt):
    """第4〜6節：E_x とブートストラップ区間、Edge Score、バッジ。"""
    s = sub[np.isfinite(sub["e"])]
    widx = {w: i for i, w in enumerate(weeks)}
    wi = s["week"].map(widx)
    if wi.isna().any():
        sys.exit("期間外の週に属するイベントがあります")
    wi = wi.to_numpy(int)
    k = len(weeks)
    sums = np.bincount(wi, weights=s["e"].to_numpy(), minlength=k)
    ns = np.bincount(wi, minlength=k).astype(float)
    est = sums.sum() / ns.sum()
    with np.errstate(invalid="ignore", divide="ignore"):
        bd = (cnt @ sums) / (cnt @ ns)
    bd = bd[np.isfinite(bd)]
    lo, hi = np.percentile(bd, [2.5, 97.5])
    if est <= 0:
        badge = "No Edge Observed"
    elif lo <= 0:
        badge = "Edge Not Established"
    else:
        badge = "Positive Edge Interval"
    y = s["ycost_H8"].to_numpy()
    pos, neg = y[y > 0].sum(), -y[y < 0].sum()
    return {
        "n": int(ns.sum()),
        "E_x": est, "E_x_lo": lo, "E_x_hi": hi,
        "edge": float(edge_score(est)), "edge_lo": float(edge_score(lo)), "edge_hi": float(edge_score(hi)),
        "badge": badge,
        "abs_net_mean": float(y.mean()),
        "baseline_mean": float(s["b"].mean()),
        "win_rate_net": float((y > 0).mean()),
        "pf_net": float(pos / neg) if neg > 0 else np.nan,
        "avg_cost_R": float((s["y_H8"] - s["ycost_H8"]).mean()),
        "_boot": bd,
    }


def build_events(ev, base):
    out = []
    for name, con, child_only, d, primary in SETS:
        sub = ev[ev["contrast"] == con]
        if child_only:
            sub = sub[sub["is_child"]]
        sub = sub.merge(base, on=["asset", "timeframe", "direction"], how="left")
        if sub["b"].isna().any():
            sys.exit(f"{name}: ベースラインが欠けている系列があります")
        sub = sub.assign(set=name, e=sub["ycost_H8"] - sub["b"], week=week_col(sub["time_utc"]))
        out.append((name, primary, sub))
    return out


def grade_phase_a(n, primary):
    return "C" if (primary and n >= 100) else "D"


def fmt(x, nd=3):
    return f"{x:+.{nd}f}"


def write_scorecard(path, scores, months):
    L = []
    L.append("# 通信簿 — スコア仕様 v0 のフェーズA参考適用\n")
    L.append("> この文書は検証記録であり、売買を勧めるものではありません。\n")
    L.append("- 採点規則：`score_spec_v0.md`（本結果の計算前に固定・タイムスタンプ済み）")
    L.append("- 期間：2024-04-01〜2026-09-30（UTC）、XAUUSD・USDJPY・BTCUSDT・ETHUSDT × H1・H4 をプール")
    L.append("- 単位：1R＝エントリー時のATR14。固定8本保有、コスト控除後")
    L.append("- 主指標：同じ資産・時間足・方向でランダムにエントリーした場合（グロス）を差し引いた超過期待値")
    L.append("- 「参考適用」：採点規則はフェーズAの絶対成績を見た後に作ったため、事前固定スコアとは呼ばない\n")
    L.append("| イベント群 | 内容 | EDGE [95%] | バッジ | Grade | Automation | n | 超過期待値 E_x (R) |")
    L.append("|---|---|---|---|---|---|---:|---|")
    for r in scores:
        L.append(f"| {r['set']} | {LABEL[r['set']]} | **{r['edge']:.0f}** [{r['edge_lo']:.0f}–{r['edge_hi']:.0f}] | "
                 f"{r['badge']} | {r['grade']} | {AUTOMATION} | {r['n']} | "
                 f"{fmt(r['E_x'])} [{fmt(r['E_x_lo'], 2)}, {fmt(r['E_x_hi'], 2)}] |")
    L.append("\n## 表示のみの項目（採点に使わない）\n")
    L.append("| イベント群 | 絶対平均 (R, コスト後) | ランダム基準 b (R) | 勝率 | PF | 平均コスト (R) | 月あたり件数 |")
    L.append("|---|---|---|---:|---:|---|---:|")
    for r in scores:
        L.append(f"| {r['set']} | {fmt(r['abs_net_mean'])} | {fmt(r['baseline_mean'])} | "
                 f"{r['win_rate_net']*100:.1f}% | {r['pf_net']:.2f} | {fmt(r['avg_cost_R'])} | {r['n']/months:.1f} |")
    L.append("\n保有時間：H1 = 8時間、H4 = 32時間（休場を除く）。月あたり件数は8系列の合計。")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def run_phase_a(args):
    start, end = PHASE_A
    ev = event_outcomes_for(args.data, start, end)
    if args.skip_verify:
        ev_pub = pd.read_csv(args.events)
        ev_pub["time_utc"] = pd.to_datetime(ev_pub["time_utc"], utc=True)
        ev = ev_pub
        print("[verify] スキップ（テスト専用）")
    else:
        verify_against_published(ev, args.events)
        ev = ev.drop(columns=["week"])
        ev["time_utc"] = pd.to_datetime(ev["time_utc"], utc=True)

    base = baseline(args.data, start, end)
    weeks = weeks_of(start, end)
    cnt = boot_counts(len(weeks), SEED_A)
    months = (end - start).days / 30.4375

    scores = []
    for name, primary, sub in build_events(ev, base):
        r = score_set(sub, weeks, cnt)
        r.pop("_boot")
        r.update({"set": name, "phase": "A", "application": "reference",
                  "grade": grade_phase_a(r["n"], primary), "automation": AUTOMATION})
        scores.append(r)

    os.makedirs(args.out, exist_ok=True)
    cols = ["set", "phase", "application", "n", "E_x", "E_x_lo", "E_x_hi", "edge", "edge_lo", "edge_hi",
            "badge", "grade", "automation", "abs_net_mean", "baseline_mean", "win_rate_net", "pf_net", "avg_cost_R"]
    pd.DataFrame(scores)[cols].to_csv(os.path.join(args.out, "phase_a_scores.csv"), index=False)
    base.to_csv(os.path.join(args.out, "phase_a_baseline.csv"), index=False)
    write_scorecard(os.path.join(args.out, "phase_a_scorecard.md"), scores, months)
    with open(os.path.join(args.out, "phase_a_run_info.txt"), "w", encoding="utf-8") as f:
        f.write(f"run_utc: {datetime.now(timezone.utc).isoformat()}\n")
        f.write(f"python: {sys.version.split()[0]}  numpy: {np.__version__}  pandas: {pd.__version__}\n")
        f.write(f"spec: score_spec_v0.md  B={B}  seed={SEED_A}  weeks={len(weeks)}\n")
        f.write(f"verified_against_published_events: {not args.skip_verify}\n")

    pd.set_option("display.width", 200)
    print(pd.DataFrame(scores)[["set", "n", "E_x", "E_x_lo", "E_x_hi", "edge", "edge_lo", "edge_hi",
                                "badge", "grade"]].round(3).to_string(index=False))


def run_phase_b(args):
    now = pd.Timestamp.now(tz="UTC")
    if now < PHASE_B_EARLIEST_RUN:
        sys.exit(f"フェーズBの評価は {PHASE_B_EARLIEST_RUN.date()} UTC 以降に限ります（prereg_v1 第6節）。")
    # フェーズA（第7節：A と B は独立にブートストラップ）
    a0, a1 = PHASE_A
    evA = event_outcomes_for(args.data, a0, a1)
    verify_against_published(evA, args.events)
    evA = evA.drop(columns=["week"])
    baseA = baseline(args.data, a0, a1)
    wA = weeks_of(a0, a1)
    cA = boot_counts(len(wA), SEED_A)
    b0, b1 = PHASE_B
    evB = event_outcomes_for(args.data, b0, b1).drop(columns=["week"])
    baseB = baseline(args.data, b0, b1)
    wB = weeks_of(b0, b1)
    cB = boot_counts(len(wB), SEED_B)

    setsA = {n: (p, s) for n, p, s in build_events(evA, baseA)}
    rows = []
    for name, primary, subB in build_events(evB, baseB):
        rA = score_set(setsA[name][1], wA, cA)
        rB = score_set(subB, wB, cB)
        d = rB["E_x"] - rA["E_x"]
        dd = rB["_boot"][:min(len(rA["_boot"]), len(rB["_boot"]))] - rA["_boot"][:min(len(rA["_boot"]), len(rB["_boot"]))]
        d_lo, d_hi = np.percentile(dd, [2.5, 97.5])
        status = "Degraded" if d_hi < 0 else "Consistent"
        width_B = rB["E_x_hi"] - rB["E_x_lo"]  # 第8節：打ち切り前の E_x で測る
        if not primary or rA["n"] < 100:
            grade = "D"
        elif status == "Degraded":
            grade = "C"
        elif width_B <= 0.20:
            grade = "A"
        else:
            grade = "B"
        rows.append({"set": name, "n_A": rA["n"], "E_x_A": rA["E_x"], "n_B": rB["n"], "E_x_B": rB["E_x"],
                     "edge_B_reference": rB["edge"], "edge_B_lo": rB["edge_lo"], "edge_B_hi": rB["edge_hi"],
                     "E_x_B_lo": rB["E_x_lo"], "E_x_B_hi": rB["E_x_hi"],
                     "d": d, "d_lo": d_lo, "d_hi": d_hi, "status": status, "grade": grade})
    os.makedirs(args.out, exist_ok=True)
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(args.out, "phase_b_check.csv"), index=False)
    baseB.to_csv(os.path.join(args.out, "phase_b_baseline.csv"), index=False)
    print(out.round(3).to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--events", default=os.path.join("results", "phase_a", "events.csv"))
    ap.add_argument("--out", default=os.path.join("results", "score_v0"))
    ap.add_argument("--phase", choices=["A", "B"], default="A")
    ap.add_argument("--skip-verify", action="store_true")
    args = ap.parse_args()
    if args.phase == "A":
        run_phase_a(args)
    else:
        run_phase_b(args)


if __name__ == "__main__":
    main()
