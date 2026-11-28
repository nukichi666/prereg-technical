import sys, os, bisect, random, datetime, statistics

# ==========================================================
# あなたの「入りの形」(ダウ理論 × 複数の時間足 × 明けの明星)を、長い期間の1分足で検証する。読み取りだけ・発注なし。
# 先に fetch_klines.py で klines_data を作っておく。使い方:
#    python dow_search.py klines_data            (全部。4銘柄 × 120通り。数分〜十数分かかる)
#    python dow_search.py klines_data quick      (HYPEだけ・設定を減らして短時間)
#
# 【結果を見る前に固定したルール。見てから動かさないこと】
# 入りの形(kind):
#   A = 大きい足(HTF=1時間か4時間)のダウが「上昇」(直近2つの山と谷がどちらも切り上がり) かつ 小さい足(trig=5分か15分)に明けの明星
#   B = A に加えて、15分足のダウが「下降」(押している最中)
#   C = 明けの明星だけ(ダウなし)         ← ダウが効いているかを見るための比較
#   R = ランダム入り(同じ出口)            ← 出口だけで勝ててしまっていないかを見るための比較
# ダウ: 山と谷は「前後2本より高い/低い足」(フラクタル)。確定は2本あとの足の終値。未来は見ない。
# 明けの明星: 足1=陰線で実体が直近20本平均の1.5倍以上 / 足2=実体が足1の30%以下 / 足3=陽線で終値が足1の実体の真ん中より上。足3の確定で入る。
# 出口と買い下がり(共通): 最初に成行で$10、そのあと入値から d bps ずつ下に指値(d=30か50)を6本(各$10)。利確=平均取得+31.5bps(指値)。
#   切り(cut): none / struct(入った時点の1時間足の直近の谷を、1分足の終値で割ったら) / drop150, drop250(入値から-150/-250bps の終値) / struct+drop150
#   安全装置: 含み損$60で成行(安値で評価)。
#   約定は保守的: 指値買いは安値が価格を下回ったとき。同じ足で買いと利確が両方ありえる場合は利確を次の足へ。損失側を先に評価。
#   費用: 成行 4.5bps(+滑り2bps) / 指値 1.5bps。資金調達は入れていない。
# 期間の分け方(各銘柄の期間を時間で): 前半50%=train / 次の25%=valid / 最後の25%=holdout。holdout は最後に1回だけ見る。
# 合格(HYPEで): train と valid の【両方】で、往復30以上・純益プラス・PF 1.2以上・安全装置の発動2%以下。
#   かつ 他の銘柄(BTC・ETH・SOL)のうち過半数で、train と valid の純益がどちらもプラス。
#   合格した設定だけ holdout を見る。holdout で往復10以上・純益プラスなら「最終合格」。
# 合格がなければ「合格なし」と出す。参考の表は合格ではない。
# 注意: 設定を120通り試す。偶然で通る数が出る。ここで効いているのは、holdout と他銘柄が合う設定だけ。資金調達と行列位置は入っていない。
# ==========================================================
LOT = 10.0
LV = 6
TP_BPS = 31.5
KILL = 60.0
TAKER = 4.5e-4
MAKER = 1.5e-4
SLIP = 2e-4
P_R = 0.01
MS_BIG = 1.5
MS_SMALL = 0.3
AVG_N = 20
CUTS = ("none", "struct", "drop150", "drop250", "struct+drop150")
DS = (30.0, 50.0)
HTFS = (60, 240)
TRIGS = (5, 15)
COINS = ["HYPE", "BTC", "ETH", "SOL"]


def load(path):
    d = {}
    with open(path, encoding="utf-8") as f:
        next(f, None)
        for ln in f:
            p = ln.strip().split(",")
            if len(p) < 6 or not p[0].isdigit():
                continue
            d[int(p[0])] = (float(p[1]), float(p[2]), float(p[3]), float(p[4]))
    ts = sorted(d)
    return ts, [d[t][0] for t in ts], [d[t][1] for t in ts], [d[t][2] for t in ts], [d[t][3] for t in ts]


class Bars:
    pass


def agg(t, o, h, l, c, tf):
    """tf分足。1分足が欠けなく tf 本そろったものだけ使う。"""
    ms = tf * 60000
    b = Bars()
    b.st, b.o, b.h, b.l, b.c, b.last, b.end = [], [], [], [], [], [], []
    i = 0
    n = len(t)
    while i < n:
        key = t[i] // ms
        j = i
        while j + 1 < n and t[j + 1] // ms == key:
            j += 1
        if j - i + 1 == tf:
            b.st.append(key * ms)
            b.o.append(o[i])
            b.h.append(max(h[i:j + 1]))
            b.l.append(min(l[i:j + 1]))
            b.c.append(c[j])
            b.last.append(j)
            b.end.append(key * ms + ms)
        i = j + 1
    return b


def dow_series(b, n=2):
    """各足の確定時点で分かるダウの状態: 1=上昇 -1=下降 0=どちらでもない/不明。と、直近に確定した谷の値。"""
    H, L = b.h, b.l
    sh, sl = [], []
    state, swl = [], []
    for j in range(len(H)):
        k = j - n
        if k >= n:
            if all(H[k] > H[k - m] and H[k] > H[k + m] for m in range(1, n + 1)):
                sh.append(H[k])
            if all(L[k] < L[k - m] and L[k] < L[k + m] for m in range(1, n + 1)):
                sl.append(L[k])
        s = 0
        if len(sh) >= 2 and len(sl) >= 2:
            if sh[-1] > sh[-2] and sl[-1] > sl[-2]:
                s = 1
            elif sh[-1] < sh[-2] and sl[-1] < sl[-2]:
                s = -1
        state.append(s)
        swl.append(sl[-1] if sl else None)
    return state, swl


def morning_star_flags(b):
    n = len(b.o)
    body = [abs(b.c[j] - b.o[j]) for j in range(n)]
    pre = [0.0]
    for x in body:
        pre.append(pre[-1] + x)
    flags = [False] * n
    for j in range(AVG_N + 2, n):
        c1, c2, c3 = j - 2, j - 1, j
        avg = (pre[c1] - pre[c1 - AVG_N]) / AVG_N
        b1 = body[c1]
        if avg <= 0:
            continue
        if (b.c[c1] < b.o[c1] and b1 >= MS_BIG * avg and body[c2] <= MS_SMALL * b1
                and b.c[c3] > b.o[c3] and b.c[c3] > (b.o[c1] + b.c[c1]) / 2.0):
            flags[j] = True
    return flags


def get_signals(kind, trig, flags, packs, seed):
    """[(1分足の添字, 1時間足(または指定足)の谷の値)]。入りの足の確定時点で分かる情報だけを使う。"""
    tb = packs[("bars", trig)]
    rnd = random.Random(seed)
    out = []
    for j in range(len(tb.o)):
        if kind == "R":
            if rnd.random() >= P_R:
                continue
            ok = True
        else:
            ok = flags[j]
        if not ok:
            continue
        sl = None
        end_ms = tb.end[j]
        if kind in ("A", "B"):
            hb, hs, hw = packs[("bars", packs["htf"])], packs[("state", packs["htf"])], packs[("swl", packs["htf"])]
            h = bisect.bisect_right(hb.end, end_ms) - 1
            if h < 0 or hs[h] != 1:
                continue
            sl = hw[h]
            if kind == "B":
                mb, ms_ = packs[("bars", 15)], packs[("state", 15)]
                m = bisect.bisect_right(mb.end, end_ms) - 1
                if m < 0 or ms_[m] != -1:
                    continue
        else:
            b60, w60 = packs[("bars", 60)], packs[("swl", 60)]
            h = bisect.bisect_right(b60.end, end_ms) - 1
            sl = w60[h] if h >= 0 else None
        out.append((tb.last[j], sl))
    return out


def run_round(i0, struct_lv, d_bps, cut, c, h, l):
    n = len(c)
    ref = c[i0]
    entry = ref * (1 + SLIP)
    qty = LOT / entry
    cost = LOT
    fee = LOT * TAKER
    levels = [ref * (1 - d_bps * k * 1e-4) for k in range(1, LV + 1)]
    filled = [False] * LV
    use_struct = cut in ("struct", "struct+drop150") and struct_lv is not None and struct_lv < ref * (1 - 5e-4)
    drop = {"drop150": 150.0, "drop250": 250.0, "struct+drop150": 150.0}.get(cut)
    mae = 0.0
    for i in range(i0 + 1, n):
        lo, hi, cl = l[i], h[i], c[i]
        fill_now = False
        for k in range(LV):
            if not filled[k] and lo < levels[k]:
                filled[k] = True
                qty += LOT / levels[k]
                cost += LOT
                fee += LOT * MAKER
                fill_now = True
        u = qty * lo - cost
        if u < mae:
            mae = u
        if u <= -KILL:
            px = lo * (1 - SLIP)
            fee += qty * px * TAKER
            return "kill", i, qty * px - cost - fee, mae
        tp = cost / qty * (1 + TP_BPS * 1e-4)
        if (not fill_now) and hi > tp:
            fee += qty * tp * MAKER
            return "tp", i, qty * tp - cost - fee, mae
        cutnow = False
        if use_struct and cl < struct_lv:
            cutnow = True
        if drop is not None and cl <= ref * (1 - drop * 1e-4):
            cutnow = True
        if cutnow:
            px = cl * (1 - SLIP)
            fee += qty * px * TAKER
            return "cut", i, qty * px - cost - fee, mae
    px = c[n - 1]
    return "open", n - 1, qty * px - cost - fee, mae


def run_config(sigs, t, d_bps, cut, c, h, l):
    res = []
    free = 0
    for (i0, sl) in sigs:
        if i0 < free:
            continue
        kind, ie, net, mae = run_round(i0, sl, d_bps, cut, c, h, l)
        res.append((t[i0], net, kind, mae))
        free = ie + 1
    return res


def seg_stats(rs):
    n = len(rs)
    nets = [r[1] for r in rs]
    pos = sum(x for x in nets if x > 0)
    neg = -sum(x for x in nets if x < 0)
    return dict(n=n, net=sum(nets), pf=(pos / neg) if neg > 0 else (float("inf") if pos > 0 else 0.0),
                kill=sum(1 for r in rs if r[2] == "kill"), cut=sum(1 for r in rs if r[2] == "cut"),
                mae=min([r[3] for r in rs], default=0.0))


def split(rs, t0, t1):
    a = t0 + (t1 - t0) * 0.5
    b = t0 + (t1 - t0) * 0.75
    return ([r for r in rs if r[0] < a], [r for r in rs if a <= r[0] < b], [r for r in rs if r[0] >= b])


def seg_ok(s):
    return s["n"] >= 30 and s["net"] > 0 and s["pf"] >= 1.2 and s["kill"] / s["n"] <= 0.02


def configs(quick):
    cf = []
    cuts = ("none", "struct", "drop150") if quick else CUTS
    ds = (30.0,) if quick else DS
    htfs = (60,) if quick else HTFS
    trigs = (15,) if quick else TRIGS
    for trig in trigs:
        for kind in ("A", "B", "C", "R"):
            for htf in (htfs if kind in ("A", "B") else (60,)):
                for d in ds:
                    for cut in cuts:
                        cf.append((kind, htf, trig, d, cut))
    return cf


def fm(ms):
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def main():
    args = sys.argv[1:]
    folder = args[0] if args else "klines_data"
    quick = "quick" in args
    coins = ["HYPE"] if quick else COINS
    cf = configs(quick)
    data = {}
    for cn in coins:
        p = os.path.join(folder, "%sUSDT_1m.csv" % cn)
        if not os.path.exists(p):
            print("%s: ファイルがありません(%s)。fetch_klines.py を先に。" % (cn, p))
            continue
        data[cn] = load(p)
    if "HYPE" not in data:
        raise SystemExit("HYPE のデータがありません")
    results = {}
    for cn, (t, o, h, l, c) in data.items():
        print("%s: 1分足 %d本 (%s 〜 %s) を処理中..." % (cn, len(t), fm(t[0]), fm(t[-1])))
        packs = {}
        flags = {}
        for tf in (5, 15, 60, 240):
            b = agg(t, o, h, l, c, tf)
            st, sw = dow_series(b)
            packs[("bars", tf)], packs[("state", tf)], packs[("swl", tf)] = b, st, sw
            if tf in TRIGS:
                flags[tf] = morning_star_flags(b)
        sig_cache = {}
        for (kind, htf, trig, d, cut) in cf:
            key = (kind, htf, trig)
            if key not in sig_cache:
                packs["htf"] = htf
                sig_cache[key] = get_signals(kind, trig, flags[trig], packs, 12345)
            rs = run_config(sig_cache[key], t, d, cut, c, h, l)
            results[(cn, kind, htf, trig, d, cut)] = split(rs, t[0], t[-1])
        print("   完了")
    print("=" * 110)
    print("試した設定: %d通り × %d銘柄。期間の分け方: 前半50%%=train / 次25%%=valid / 最後25%%=holdout(最後に合格分だけ見る)" % (len(cf), len(data)))
    # 入りの形ごとの中央値(ダウやランダムとの比較)
    print("-" * 110)
    print("【入りの形ごとの比較(HYPE・全設定の中央値)】 ダウが効いているなら A/B が C を上回り、R(ランダム)より良いはず")
    for kind in ("A", "B", "C", "R"):
        rows = [results[k] for k in results if k[0] == "HYPE" and k[1] == kind]
        if not rows:
            continue
        tr = [seg_stats(r[0]) for r in rows]
        va = [seg_stats(r[1]) for r in rows]
        print("  %s: train 純益の中央値 %+8.1f(プラスの設定 %d/%d) / valid 純益の中央値 %+8.1f(プラス %d/%d) / 往復数の中央値 train %d valid %d" % (
            kind, statistics.median(x["net"] for x in tr), sum(1 for x in tr if x["net"] > 0), len(tr),
            statistics.median(x["net"] for x in va), sum(1 for x in va if x["net"] > 0), len(va),
            statistics.median(x["n"] for x in tr), statistics.median(x["n"] for x in va)))
    # 合格判定
    others = [c for c in data if c != "HYPE"]
    passed = []
    funnel = [0, 0, 0]
    for k in results:
        if k[0] != "HYPE":
            continue
        tr, va, ho = results[k]
        s_tr, s_va = seg_stats(tr), seg_stats(va)
        if not (seg_ok(s_tr) and seg_ok(s_va)):
            continue
        funnel[0] += 1
        good = 0
        for oc in others:
            o_tr, o_va, _ = results.get((oc,) + k[1:], ([], [], []))
            if seg_stats(o_tr)["net"] > 0 and seg_stats(o_va)["net"] > 0:
                good += 1
        if others and good * 2 <= len(others):
            continue
        funnel[1] += 1
        passed.append((k, s_tr, s_va, seg_stats(ho), good))
    print("-" * 110)
    print("【合格の絞り込み】 HYPEで train・valid とも合格: %d通り → 他銘柄の過半数でも合格: %d通り → holdout も合格: " % (funnel[0], funnel[1]), end="")
    final = [p for p in passed if p[3]["n"] >= 10 and p[3]["net"] > 0]
    print("%d通り" % len(final))
    if not passed:
        print("合格なし。この入りの形は、このルールの範囲では、勝てるとは言えない。")
    for (k, s_tr, s_va, s_ho, good) in sorted(passed, key=lambda x: -x[2]["net"])[:15]:
        mark = "最終合格" if (s_ho["n"] >= 10 and s_ho["net"] > 0) else "holdoutで不合格"
        print("  %s HTF%3dm trig%2dm d%2d cut=%-14s | train n%3d net%+7.1f pf%.2f | valid n%3d net%+7.1f pf%.2f | holdout n%3d net%+7.1f pf%.2f 最悪MAE%+6.1f | 他銘柄%d/%d | %s" % (
            k[1], k[2], k[3], k[4], k[5], s_tr["n"], s_tr["net"], s_tr["pf"], s_va["n"], s_va["net"], s_va["pf"],
            s_ho["n"], s_ho["net"], s_ho["pf"], s_ho["mae"], good, len(others), mark))
    # 参考(合格ではない): validの純益が大きい順
    print("-" * 110)
    print("【参考・合格ではない】HYPEで valid 純益が大きい5通り(往復30以上のみ)。この並べ方は後付けで、採用の根拠にならない。")
    cand = []
    for k, (tr, va, ho) in results.items():
        if k[0] != "HYPE":
            continue
        s_va = seg_stats(va)
        if s_va["n"] >= 30:
            cand.append((s_va["net"], k, seg_stats(tr), s_va))
    for net, k, s_tr, s_va in sorted(cand, key=lambda x: -x[0])[:5]:
        print("  %s HTF%3dm trig%2dm d%2d cut=%-14s | train n%3d net%+7.1f | valid n%3d net%+7.1f pf%.2f" % (
            k[1], k[2], k[3], k[4], k[5], s_tr["n"], s_tr["net"], s_va["n"], s_va["net"], s_va["pf"]))
    print("=" * 110)
    print("読み方: 最終合格が出ても採用は決めない。そのまま前向き(これから先のデータ)で、数字を固定して試す。最終合格がなければ、この形は今のルールでは通らなかった、が答え。")
    print("金額は1回$10(最大$70)での数字。大きさを変えても、手数料以外はほぼ比例する(損も同じ倍率)。")


if __name__ == "__main__":
    main()
