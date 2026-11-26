"""3本RCIの押し目買い（ドル円4時間足）採点 — 事前固定 tsushinbo/3本RCI押し目買い_事前固定_2026-11-13 に従う。
使い方: python3 rci_backtest.py <csv> <出力json>
"""
import csv, json, sys
from datetime import datetime, timezone
import numpy as np

CSV, OUT = sys.argv[1], sys.argv[2]
PIP = 0.01

# ---- データ読み込み（値動きのない足は除く） ----
ts, O, H, L, C = [], [], [], [], []
with open(CSV) as f:
    r = csv.reader(f); next(r)
    for row in r:
        o, h, l, c = map(float, row[1:5])
        if o == h == l == c:
            continue
        ts.append(datetime.fromisoformat(row[0].replace("Z", "+00:00")))
        O.append(o); H.append(h); L.append(l); C.append(c)
O, H, L, C = map(np.array, (O, H, L, C))
N = len(C)


def rank_avg(a):
    order = a.argsort(kind="mergesort")
    ranks = np.empty(len(a)); ranks[order] = np.arange(1, len(a) + 1)
    # 同じ値は平均順位
    vals, inv, cnt = np.unique(a, return_inverse=True, return_counts=True)
    if (cnt > 1).any():
        sums = np.zeros(len(vals)); np.add.at(sums, inv, ranks)
        ranks = (sums / cnt)[inv]
    return ranks


def rci(n):
    out = np.full(N, np.nan)
    trank = np.arange(1, n + 1, dtype=float)  # 古い→新しい（新しいほど大）
    for i in range(n - 1, N):
        pr = rank_avg(C[i - n + 1:i + 1])
        if pr.std() == 0:
            out[i] = 0.0
            continue
        out[i] = np.corrcoef(trank, pr)[0, 1] * 100
    return out


R9, R26, R52 = rci(9), rci(26), rci(52)
ma = np.full(N, np.nan); sd = np.full(N, np.nan)
for i in range(19, N):
    w = C[i - 19:i + 1]; ma[i] = w.mean(); sd[i] = w.std(ddof=0)
UP, DN = ma + 2 * sd, ma - 2 * sd


def signals(side, typ):
    """各足 t について、シグナルなら True（確定足で判定）。"""
    sig = np.zeros(N, bool)
    s = 1 if side == "long" else -1
    A = None; stage = 0; flag80 = False
    for i in range(52, N):
        r9, r26, r52 = R9[i], R26[i], R52[i]
        if np.isnan(r52) or np.isnan(UP[i]):
            continue
        tamari = (r9 <= -80 and r26 <= -80 and r52 <= -80) if s == 1 else (r9 >= 80 and r26 >= 80 and r52 >= 80)
        if tamari:
            A = i; stage = 1; flag80 = False
            continue  # B は A より後
        if A is None:
            continue
        if i - A > 40:
            A = None; stage = 0; continue
        if stage == 1:
            order = (r9 > r26 > r52) if s == 1 else (r52 > r26 > r9)
            if order:
                stage = 2  # 同じ足で C も見る（B 以降）
        if stage == 2:
            strong = (H[i] >= UP[i]) if s == 1 else (L[i] <= DN[i])
            if strong:
                stage = 3; flag80 = False
                if (s == 1 and r9 >= 80) or (s == -1 and r9 <= -80):
                    flag80 = True
                continue  # D は C より後
        if stage >= 3:
            if (s == 1 and r9 >= 80) or (s == -1 and r9 <= -80):
                flag80 = True
        if stage == 3:
            if (r9 < 0) if s == 1 else (r9 > 0):
                stage = 4  # 同じ足で上向きも見る（D 以降）
        if stage == 4:
            turn = (R9[i] > R9[i - 1]) if s == 1 else (R9[i] < R9[i - 1])
            if turn:
                if typ == 1 or flag80:
                    sig[i] = True
                A = None; stage = 0
    return sig


def simulate(typ, spread_pips, start, end):
    sp = spread_pips * PIP
    sl_sig = signals("long", typ); ss_sig = signals("short", typ)
    trades = []; pos = None; open_at_end = 0; skipped_small = 0
    for i in range(N):
        # 保有中なら決済判定（この足で）
        if pos is not None and i >= pos["ei"]:
            d = pos["side"]
            if d == 1:
                lo, hi, op = L[i], H[i], O[i]
                if op <= pos["sl"]:
                    ex = op
                elif lo <= pos["sl"]:
                    ex = pos["sl"]
                elif hi >= pos["tp"]:
                    ex = pos["tp"]
                else:
                    ex = None
            else:
                lo, hi, op = L[i] + sp, H[i] + sp, O[i] + sp  # Ask
                if op >= pos["sl"]:
                    ex = op
                elif hi >= pos["sl"]:
                    ex = pos["sl"]
                elif lo <= pos["tp"]:
                    ex = pos["tp"]
                else:
                    ex = None
            if ex is not None:
                R = d * (ex - pos["entry"]) / pos["risk"]
                trades.append({"side": "long" if d == 1 else "short", "entry_time": ts[pos["ei"]].isoformat(),
                               "exit_time": ts[i].isoformat(), "entry": pos["entry"], "sl": pos["sl"],
                               "tp": pos["tp"], "exit": ex, "R": R, "risk_pips": pos["risk"] / PIP})
                pos = None
        # シグナル（足 t の終わりに持っていなければ）→ 次の足の始値で入る
        if pos is None and i + 1 < N and (sl_sig[i] or ss_sig[i]):
            et = ts[i + 1]
            if not (start <= et < end):
                continue
            d = 1 if sl_sig[i] else -1  # 両方同時なら買いを優先（記録）
            if d == 1:
                entry = O[i + 1] + sp
                sl = L[i - 19:i + 1].min()
                risk = entry - sl
            else:
                entry = O[i + 1]
                sl = H[i - 19:i + 1].max()
                risk = sl - entry
            if risk < 5 * PIP:
                skipped_small += 1
                continue
            tp = entry + d * 1.5 * risk
            pos = {"side": d, "ei": i + 1, "entry": entry, "sl": sl, "tp": tp, "risk": risk}
    if pos is not None:
        open_at_end = 1
    return trades, open_at_end, skipped_small, int((sl_sig & ss_sig).sum())


def stats(trades, seed=20261113):
    Rs = np.array([t["R"] for t in trades])
    n = len(Rs)
    if n == 0:
        return {"n": 0}
    wins = (Rs > 0).sum()
    pf = Rs[Rs > 0].sum() / -Rs[Rs < 0].sum() if (Rs < 0).any() else float("inf")
    eq = 1_000_000.0; peak = eq; mdd = 0.0; curve = []
    for R in Rs:
        eq += eq * 0.02 * R; curve.append(eq)
        peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
    yen_pnl = np.diff([1_000_000.0] + curve)
    yen_pf = yen_pnl[yen_pnl > 0].sum() / -yen_pnl[yen_pnl < 0].sum() if (yen_pnl < 0).any() else float("inf")
    streak = mx = 0
    for R in Rs:
        streak = streak + 1 if R <= 0 else 0; mx = max(mx, streak)
    rng = np.random.default_rng(seed)
    boots = rng.choice(Rs, size=(10000, n), replace=True).mean(axis=1)
    p = float((boots <= 0).mean())
    out = {"n": int(n), "wins": int(wins), "winrate": float(wins / n), "meanR": float(Rs.mean()),
           "medianR": float(np.median(Rs)), "pf_R": float(pf), "pf_yen": float(yen_pf), "p_boot": p,
           "maxDD_pct": float(mdd * 100), "max_losing_streak": int(mx), "final_yen": float(eq),
           "mean_risk_pips": float(np.mean([t["risk_pips"] for t in trades]))}
    for sd in ("long", "short"):
        sub = [t["R"] for t in trades if t["side"] == sd]
        out[sd] = {"n": len(sub), "winrate": float(np.mean(np.array(sub) > 0)) if sub else None,
                   "meanR": float(np.mean(sub)) if sub else None, "sumR": float(np.sum(sub)) if sub else 0.0}
    return out


def verdict(s):
    if s["n"] < 50:
        return "件数不足"
    if s["meanR"] <= 0:
        return "再現せず"
    if s["p_boot"] < 0.025 and s["pf_R"] >= 1.2:
        return "再現"
    return "一部再現"


utc = timezone.utc
MAIN = (datetime(2016, 1, 1, tzinfo=utc), datetime(2026, 11, 1, tzinfo=utc))
REF = (datetime(2010, 1, 1, tzinfo=utc), datetime(2016, 1, 1, tzinfo=utc))
res = {"bars_used": N, "first_bar": ts[0].isoformat(), "last_bar": ts[-1].isoformat(), "runs": {}}
for typ in (1, 2):
    for sp in (1.6, 0.8, 3.0):
        for name, (a, b) in (("main", MAIN), ("ref", REF)):
            tr, oe, sk, both = simulate(typ, sp, a, b)
            s = stats(tr)
            if s["n"]:
                s["verdict"] = verdict(s)
            s.update({"open_at_end": oe, "skipped_small_stop": sk, "both_side_signal_bars": both})
            key = f"type{typ}_spread{sp}_{name}"
            res["runs"][key] = s
            if typ in (1, 2) and sp == 1.6:
                res.setdefault("trades", {})[key] = tr
json.dump(res, open(OUT, "w"), ensure_ascii=False, indent=1)
for k, v in res["runs"].items():
    if v["n"]:
        print(k, v["n"], f'{v["winrate"]:.3f}', f'{v["meanR"]:+.3f}', f'PF{v["pf_R"]:.2f}', f'p{v["p_boot"]:.4f}',
              f'DD{v["maxDD_pct"]:.1f}%', f'{v["final_yen"]:,.0f}', v["verdict"], "L", v["long"]["n"], "S", v["short"]["n"])
    else:
        print(k, 0)
