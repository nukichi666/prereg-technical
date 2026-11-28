import sys, os, datetime

import dow_search as ds

# ==========================================================
# dow_search.py で「最終合格」が出た形(切りなし)の、中身の確認。読み取りだけ・発注なし。
# 見たいこと: ①全期間での最悪の含み損(dow_search の表は holdout だけだった) ②保有が長引いた往復 ③四半期ごとに安定しているか
#            ④銘柄そのものの上げ下げと比べて、上げ相場でしか勝てていないのでは? ⑤資金調達を引いても残るか ⑥資金$300でいくらの大きさまで耐えられるか
# 使い方: python dow_tail.py klines_data
#
# 【結果を見る前に固定したルール】
#  対象: 切りなし(cut=none)の全24通り(A・B・C・R)。ここで新しく設定を選ばない。
#  資金調達: 1時間あたり 0.00125%(ロングが払う向き)を、往復で投入した額 × 保有時間 に掛ける。(多めに見積もる)
#  大きさの合わせ方: 全期間の最悪の含み損が資金$300の50%($150)になるように、1回の額を k 倍する。k = 150 ÷ 最悪の含み損。
#  k の上限: 最大で持つ額(1回$10×7=$70)の k 倍が、資金の6倍($1,800。ウォレットの約6.4倍と同程度)を超えない範囲。k ≤ 1800 ÷ 70 = 25.7。
#  採用候補の条件(全部満たす): ⓪往復が30以上 ①k倍にして、資金調達込みの年間純益が $60 以上(資金の20%/年) ②四半期ごとの純益がプラスの四半期が過半数
#                              ③データ終わりで戻っていない往復の含み損が、k倍で $150 以内
#  満たすものがなければ「採用候補なし」。満たしても採用は決めない。前向きで試す。
#
# 【前向きテスト(開始日指定)】 python dow_tail.py klines_data since=2026-10-15
#  開始日(日本時間0時)より後に出た入りだけで、HYPEの切りなし24通りを数え直す。設定は選び直さない。判定はAの8通りだけ(事前に固定):
#   期間が28日以上 かつ 8通り中6通り以上が資金調達込みでプラス(往復0はプラスに数えない) かつ 8通りの純益の中央値がプラス かつ 最悪の含み損が -$25 より浅い。
#   満たさなければ「Aの形は採用しない」。期間が足りないうちは「判定不能(あと◯日)」。結果を見てから基準を動かさない。
# 注意: 往復は1本ずつ順番に(同時に1ポジション)。約定は保守的な1分足の近似で、行列位置・滑りの大きい局面・資金調達の変動は入っていない。
#       取引所の1分足が欠けている時間は、その足がなかったものとして進む。
# ==========================================================
FUND_PER_H = 1.25e-5
CAPITAL = 300.0
MAE_CAP = 150.0
MIN_ANNUAL = 60.0
KMAX = 6 * CAPITAL / (ds.LOT * (ds.LV + 1))
MIN_N = 30
REPS = [("C", 60, 15, 30.0, "none"), ("R", 60, 15, 30.0, "none"), ("A", 60, 5, 30.0, "none")]


def run_round2(i0, struct_lv, d_bps, cut, c, h, l):
    """ds.run_round と同じ計算。違いは、最後に投入額(cost)も返すこと。"""
    n = len(c)
    ref = c[i0]
    entry = ref * (1 + ds.SLIP)
    qty = ds.LOT / entry
    cost = ds.LOT
    fee = ds.LOT * ds.TAKER
    levels = [ref * (1 - d_bps * k * 1e-4) for k in range(1, ds.LV + 1)]
    filled = [False] * ds.LV
    use_struct = cut in ("struct", "struct+drop150") and struct_lv is not None and struct_lv < ref * (1 - 5e-4)
    drop = {"drop150": 150.0, "drop250": 250.0, "struct+drop150": 150.0}.get(cut)
    mae = 0.0
    for i in range(i0 + 1, n):
        lo, hi, cl = l[i], h[i], c[i]
        fill_now = False
        for k in range(ds.LV):
            if not filled[k] and lo < levels[k]:
                filled[k] = True
                qty += ds.LOT / levels[k]
                cost += ds.LOT
                fee += ds.LOT * ds.MAKER
                fill_now = True
        u = qty * lo - cost
        if u < mae:
            mae = u
        if u <= -ds.KILL:
            px = lo * (1 - ds.SLIP)
            fee += qty * px * ds.TAKER
            return "kill", i, qty * px - cost - fee, mae, cost
        tp = cost / qty * (1 + ds.TP_BPS * 1e-4)
        if (not fill_now) and hi > tp:
            fee += qty * tp * ds.MAKER
            return "tp", i, qty * tp - cost - fee, mae, cost
        cutnow = False
        if use_struct and cl < struct_lv:
            cutnow = True
        if drop is not None and cl <= ref * (1 - drop * 1e-4):
            cutnow = True
        if cutnow:
            px = cl * (1 - ds.SLIP)
            fee += qty * px * ds.TAKER
            return "cut", i, qty * px - cost - fee, mae, cost
    px = c[n - 1]
    return "open", n - 1, qty * px - cost - fee, mae, cost


def run_config2(sigs, t, d_bps, cut, c, h, l):
    res = []
    free = 0
    for (i0, sl) in sigs:
        if i0 < free:
            continue
        kind, ie, net, mae, cost = run_round2(i0, sl, d_bps, cut, c, h, l)
        hours = (t[ie] - t[i0]) / 3600000.0
        fund = cost * hours * FUND_PER_H
        res.append(dict(t0=t[i0], t1=t[ie], net=net, fund=fund, kind=kind, mae=mae, hours=hours))
        free = ie + 1
    return res


def build(t, o, h, l, c):
    packs, flags = {}, {}
    for tf in (5, 15, 60, 240):
        b = ds.agg(t, o, h, l, c, tf)
        st, sw = ds.dow_series(b)
        packs[("bars", tf)], packs[("state", tf)], packs[("swl", tf)] = b, st, sw
        if tf in ds.TRIGS:
            flags[tf] = ds.morning_star_flags(b)
    return packs, flags


def quarter(ms):
    d = datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc)
    return (d.year, (d.month - 1) // 3 + 1)


def asset_quarters(t, c):
    q = {}
    for i in range(len(t)):
        k = quarter(t[i])
        if k not in q:
            q[k] = [c[i], c[i]]
        q[k][1] = c[i]
    return {k: (v[1] / v[0] - 1) * 100 for k, v in q.items()}


def summarize(rs, t):
    n = len(rs)
    net = sum(r["net"] for r in rs)
    fund = sum(r["fund"] for r in rs)
    years = max((t[-1] - t[0]) / (365.25 * 86400000.0), 1e-9)
    worst = min([r["mae"] for r in rs], default=0.0)
    wr = min(rs, key=lambda r: r["mae"]) if rs else None
    opens = [r for r in rs if r["kind"] == "open"]
    open_unreal = sum(r["net"] for r in opens)
    hold_max = max([r["hours"] for r in rs], default=0.0) / 24.0
    hold_avg = (sum(r["hours"] for r in rs) / n) if n else 0.0
    qn = {}
    for r in rs:
        qn[quarter(r["t0"])] = qn.get(quarter(r["t0"]), 0.0) + r["net"] - r["fund"]
    pos_q = sum(1 for v in qn.values() if v > 0)
    k = (MAE_CAP / -worst) if worst < 0 else KMAX
    k = min(k, KMAX)
    ann_net = (net - fund) / years
    proj = k * ann_net
    ok0 = n >= MIN_N
    ok1 = proj >= MIN_ANNUAL
    ok2 = len(qn) > 0 and pos_q * 2 > len(qn)
    ok3 = k * open_unreal >= -MAE_CAP
    return dict(n=n, net=net, fund=fund, worst=worst, worst_t=(wr["t0"] if wr else None), hold_max=hold_max, hold_avg=hold_avg,
                opens=len(opens), open_unreal=open_unreal, qn=qn, pos_q=pos_q, k=k, proj=proj, ok=(ok0 and ok1 and ok2 and ok3),
                ok0=ok0, ok1=ok1, ok2=ok2, ok3=ok3, years=years)


def fmd(ms):
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def forward_main(folder, since):
    t_since = datetime.datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=datetime.timezone(datetime.timedelta(hours=9))).timestamp() * 1000
    p = os.path.join(folder, "HYPEUSDT_1m.csv")
    if not os.path.exists(p):
        raise SystemExit("HYPE のデータがありません")
    t, o, h, l, c = ds.load(p)
    days = (t[-1] - t_since) / 86400000.0
    print("前向きテスト: 開始 %s 以降 / データの終わり %s / 経過 %.1f日" % (since, datetime.datetime.fromtimestamp(t[-1] / 1000).strftime("%Y-%m-%d %H:%M"), days))
    if days <= 0:
        raise SystemExit("開始日より後のデータがありません。fetch_klines.py で最新まで取り直してください。")
    packs, flags = build(t, o, h, l, c)
    cache = {}
    rows = []
    for kind in ("A", "B", "C", "R"):
        for htf in ((60, 240) if kind in ("A", "B") else (60,)):
            for trig in ds.TRIGS:
                for d in ds.DS:
                    key = (kind, htf, trig)
                    if key not in cache:
                        packs["htf"] = htf
                        cache[key] = [x for x in ds.get_signals(kind, trig, flags[trig], packs, 12345) if t[x[0]] >= t_since]
                    rs = run_config2(cache[key], t, d, "none", c, h, l)
                    n = len(rs)
                    net = sum(r["net"] - r["fund"] for r in rs)
                    worst = min([r["mae"] for r in rs], default=0.0)
                    opens = [r for r in rs if r["kind"] == "open"]
                    rows.append((kind, htf, trig, d, n, net, worst, len(opens), sum(r["net"] for r in opens)))
    print("=" * 110)
    print("  形  HTF trig  d | 往復   純益(資金調達込み) | 最悪の含み損 | 未決済 その含み損")
    for r in rows:
        print("  %s %4dm %2dm %2d | %4d %+9.2f | %+8.1f | %d %+7.1f" % (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8]))
    a = [r for r in rows if r[0] == "A"]
    pos = sum(1 for r in a if r[5] > 0 and r[4] > 0)
    nets = sorted(r[5] for r in a)
    med = (nets[3] + nets[4]) / 2.0
    worst = min(r[6] for r in a)
    print("-" * 110)
    print("Aの8通り: プラス %d/8 / 純益の中央値 %+.2f / 最悪の含み損 %+.1f / 往復の合計 %d" % (pos, med, worst, sum(r[4] for r in a)))
    if days < 28:
        print("判定不能(あと %.1f日。28日たってから判定)。いまの数字は途中経過で、判定には使わない。" % (28 - days))
    else:
        ok = pos >= 6 and med > 0 and worst > -25.0
        print("判定: %s (基準: プラス6/8以上・中央値プラス・最悪の含み損が -$25 より浅い)" % ("合格(ただし採用は別。次の確認へ)" if ok else "不合格(Aの形は採用しない)"))


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "klines_data"
    data = {}
    for cn in ds.COINS:
        p = os.path.join(folder, "%sUSDT_1m.csv" % cn)
        if os.path.exists(p):
            data[cn] = ds.load(p)
    if "HYPE" not in data:
        raise SystemExit("HYPE のデータがありません")
    cfgs = []
    for kind in ("A", "B", "C", "R"):
        for htf in ((60, 240) if kind in ("A", "B") else (60,)):
            for trig in ds.TRIGS:
                for d in ds.DS:
                    cfgs.append((kind, htf, trig, d, "none"))
    out = {}
    aq = {}
    for cn, (t, o, h, l, c) in data.items():
        print("%s を処理中..." % cn)
        packs, flags = build(t, o, h, l, c)
        aq[cn] = asset_quarters(t, c)
        use = cfgs if cn == "HYPE" else REPS
        cache = {}
        for (kind, htf, trig, d, cut) in use:
            key = (kind, htf, trig)
            if key not in cache:
                packs["htf"] = htf
                cache[key] = ds.get_signals(kind, trig, flags[trig], packs, 12345)
            rs = run_config2(cache[key], t, d, cut, c, h, l)
            out[(cn, kind, htf, trig, d, cut)] = (summarize(rs, t), rs)
        print("   完了(%s 〜 %s・%.2f年・買って持つだけの場合 %+.0f%%)" % (fmd(t[0]), fmd(t[-1]), (t[-1] - t[0]) / 31557600000.0, (c[-1] / c[0] - 1) * 100))
    print("=" * 128)
    print("【HYPE・切りなし24通り・全期間】 1回$10(最大$70)での数字。資金調達込み。k=最悪の含み損を$150に合わせる倍率")
    print("  形  HTF trig  d | 往復   純益  資金調達 | 最悪の含み損(いつ) | 最長保有 平均保有 | 未決済 その含み損 | 四半期+ | k倍  年間純益(k倍) | 条件")
    n_ok = 0
    for key in sorted((k for k in out if k[0] == "HYPE"), key=lambda k: (k[1], k[2], k[3], k[4])):
        s = out[key][0]
        mark = "採用候補" if s["ok"] else "×" + ("" if s["ok0"] else "往復少") + ("" if s["ok1"] else "利益") + ("" if s["ok2"] else "四半期") + ("" if s["ok3"] else "未決済")
        n_ok += 1 if s["ok"] else 0
        print("  %s %4dm %2dm %2d | %4d %+7.1f %+7.2f | %+7.1f (%s) | %6.1f日 %6.1f時間 | %d %+7.1f | %d/%d | %5.1f %+8.1f | %s" % (
            key[1], key[2], key[3], key[4], s["n"], s["net"], -s["fund"], s["worst"], fmd(s["worst_t"]) if s["worst_t"] else "-",
            s["hold_max"], s["hold_avg"], s["opens"], s["open_unreal"], s["pos_q"], len(s["qn"]),
            s["k"], s["proj"], mark))
    print("-" * 128)
    byk = {}
    for key in out:
        if key[0] == "HYPE":
            byk.setdefault(key[1], [0, 0])
            byk[key[1]][1] += 1
            byk[key[1]][0] += 1 if out[key][0]["ok"] else 0
    print("入りの形ごとの採用候補の数(ランダム入りRと比べる): " + " / ".join("%s %d/%d" % (k, v[0], v[1]) for k, v in sorted(byk.items())))
    print("採用候補: %d / 24通り(条件: k倍で年間純益 $%.0f 以上 かつ 四半期の過半数がプラス かつ 未決済の含み損が k倍で $%.0f 以内)" % (n_ok, MIN_ANNUAL, MAE_CAP))
    print("=" * 128)
    print("【四半期ごとの内訳(HYPE・代表3通り)】 形のあとの数字は 往復 / 純益(資金調達込み)。右端は、その四半期に買って持つだけの場合の値動き(%)")
    for rep in REPS:
        key = ("HYPE",) + rep
        s = out[key][0]
        print("  %s HTF%dm trig%dm d%d:" % (rep[0], rep[1], rep[2], rep[3]))
        cnt = {}
        for r in out[key][1]:
            q = quarter(r["t0"])
            cnt[q] = cnt.get(q, 0) + 1
        for q in sorted(s["qn"]):
            print("     %d年Q%d: 往復%4d 純益%+7.2f | 銘柄の値動き %+6.1f%%" % (q[0], q[1], cnt.get(q, 0), s["qn"][q], aq["HYPE"].get(q, 0.0)))
    print("=" * 128)
    print("【他の銘柄・代表3通り・全期間】(同じ形・同じ数字。k倍と年間純益はその銘柄の最悪の含み損で計算)")
    for cn in data:
        if cn == "HYPE":
            continue
        for rep in REPS:
            s = out[(cn,) + rep][0]
            print("  %-4s %s HTF%dm trig%dm d%d | 往復%5d 純益%+8.1f 資金調達%+7.2f | 最悪の含み損%+7.1f (%s) 最長保有%6.1f日 | 四半期+ %d/%d | 未決済%d(含み損%+.1f) | k%.1f 年間(k倍)%+.1f" % (
                cn, rep[0], rep[1], rep[2], rep[3], s["n"], s["net"], -s["fund"], s["worst"], fmd(s["worst_t"]) if s["worst_t"] else "-", s["hold_max"],
                s["pos_q"], len(s["qn"]), s["opens"], s["open_unreal"], s["k"], s["proj"]))
    print("=" * 128)
    print("読み方: 採用候補が出ても採用は決めない。(1) 最悪の含み損が深く、保有が数日〜数週間に及ぶなら『切らずに待つ』ことで勝っている形。 (2) 四半期ごとの純益が、銘柄の上げ下げと連動していないか。 (3) ランダム入り(R)と形(A・B・C)に差がなければ、入りの形ではなく『下で拾って待つ』構造が利益の出どころ。")


if __name__ == "__main__":
    since = None
    for a in sys.argv[2:]:
        if a.startswith("since="):
            since = a[6:]
    if since:
        forward_main(sys.argv[1] if len(sys.argv) > 1 else "klines_data", since)
    else:
        main()
