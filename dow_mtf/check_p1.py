import datetime, collections
import dow_search as ds
import dow_tail as dt

# 第1期だけを数え直す(読み取りだけ)。データは 2026-11-13 15:54 JST で切るので、第2期(11/14〜)のデータは使わない。
JST = datetime.timezone(datetime.timedelta(hours=9))
SINCE = datetime.datetime(2026, 10, 15, tzinfo=JST).timestamp() * 1000
END = datetime.datetime(2026, 11, 13, 15, 54, tzinfo=JST).timestamp() * 1000
t, o, h, l, c = ds.load("klines_data/HYPEUSDT_1m.csv")
m = max(i for i, x in enumerate(t) if x <= END) + 1
t, o, h, l, c = t[:m], o[:m], h[:m], l[:m], c[:m]
f = lambda ms: datetime.datetime.fromtimestamp(ms / 1000, JST).strftime("%Y-%m-%d %H:%M")
i0 = next(i for i, x in enumerate(t) if x >= SINCE)
print("使ったデータの終わり %s / 期間の初めの足 %s 終値 %.3f / 終わりの足 %s 終値 %.3f / 期間中の最安値 %.3f 最高値 %.3f" % (
    f(t[-1]), f(t[i0]), c[i0], f(t[-1]), c[-1], min(l[i0:]), max(h[i0:])))
packs, flags = dt.build(t, o, h, l, c)
print("形 HTF trig d | 往復 | 純益(資金調達込み) | 決済済み 件数 純益(資金調達込み) | 未決済 件数 含み損 資金調達 | 最悪の含み損 | 決済の内訳")
for kind in ("A", "B", "C", "R"):
    for htf in ((60, 240) if kind in ("A", "B") else (60,)):
        for trig in ds.TRIGS:
            packs["htf"] = htf
            sigs = [x for x in ds.get_signals(kind, trig, flags[trig], packs, 12345) if t[x[0]] >= SINCE]
            for d in ds.DS:
                rs = dt.run_config2(sigs, t, d, "none", c, h, l)
                cl = [r for r in rs if r["kind"] != "open"]
                op = [r for r in rs if r["kind"] == "open"]
                tot = sum(r["net"] - r["fund"] for r in rs)
                worst = min([r["mae"] for r in rs], default=0.0)
                kinds = dict(collections.Counter(r["kind"] for r in rs))
                print("%s %4dm %2dm %2d | %3d | %+8.3f | %3d %+8.3f | %d %+8.3f %8.4f | %+7.2f | %s" % (
                    kind, htf, trig, d, len(rs), tot, len(cl), sum(r["net"] - r["fund"] for r in cl),
                    len(op), sum(r["net"] for r in op), sum(r["fund"] for r in op), worst, kinds))
