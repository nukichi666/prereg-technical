"""BTC サイン採点(PREREG.md 2026-11-10 の条件どおり)

  python3 score.py data/      → 結果を表示し、results.json と events.csv を書く
乱数の種は固定(20261110)。同じデータなら同じ結果になる。
"""
import csv, json, sys, os, random, datetime, statistics as st

D = sys.argv[1] if len(sys.argv) > 1 else 'data'
END = '2026-11-09'
FEE = 0.001
N_RAND = 10_000
ALPHA = 0.05 / 4
random.seed(20261110)


def load_bars(path):
    rows = [r for r in csv.DictReader(open(path)) if r['open_time_utc'] <= END]
    return [r['open_time_utc'] for r in rows], [float(r['open']) for r in rows], [float(r['close']) for r in rows]


def rsi_wilder(close, n=14):
    out = [None] * len(close)
    gains = [max(close[i] - close[i - 1], 0) for i in range(1, len(close))]
    losses = [max(close[i - 1] - close[i], 0) for i in range(1, len(close))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    out[n] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(close)):
        ag = (ag * (n - 1) + gains[i - 1]) / n
        al = (al * (n - 1) + losses[i - 1]) / n
        out[i] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def ret(op, e, h):
    return op[e + h] / op[e] - 1 - FEE


def evaluate(name, dates, op, raw_signal_idx, first_ok_idx, horizons, primary, cooldown):
    """raw_signal_idx: 条件が成り立った足の番号(昇順)。first_ok_idx: データがそろう最初の足の番号"""
    n = len(op)
    # 重複を除く
    events, last = [], None
    for i in raw_signal_idx:
        if last is not None and i - last < cooldown:
            continue
        events.append(i); last = i
    res = {'name': name, 'raw_signals': len(raw_signal_idx), 'events_all': len(events), 'horizons': {}}
    for h in horizons:
        ev = [i for i in events if i + 1 + h <= n - 1]           # 保有期間が終わるものだけ
        rs = [ret(op, i + 1, h) for i in ev]
        pool = [ret(op, i + 1, h) for i in range(first_ok_idx, n) if i + 1 + h <= n - 1]
        k = len(rs)
        r = {'n': k, 'period': f'{dates[first_ok_idx]}〜{dates[n - 1]}', 'random_pool': len(pool)}
        if k:
            m = st.mean(rs)
            rnd = [st.mean(random.sample(pool, k)) for _ in range(N_RAND)]
            p = sum(1 for x in rnd if x >= m) / N_RAND
            r.update(mean=m, median=st.median(rs), win=sum(1 for x in rs if x > 0) / k, worst=min(rs), best=max(rs),
                     random_mean=st.mean(pool), random_win=sum(1 for x in pool if x > 0) / len(pool), p=p)
            if h == primary:
                if k < 10:
                    r['verdict'] = '件数不足'
                elif p < ALPHA and m > 0:
                    r['verdict'] = '合格'
                elif p < 0.05:
                    r['verdict'] = '参考どまり'
                else:
                    r['verdict'] = '不合格'
        r['events'] = [(dates[i], dates[i + 1], round(ret(op, i + 1, h), 4)) for i in ev]
        res['horizons'][h] = r
    res['primary'] = primary
    return res


def main():
    dd, dop, dcl = load_bars(os.path.join(D, 'btc_1d.csv'))
    wd, wop, wcl = load_bars(os.path.join(D, 'btc_1w.csv'))
    didx = {d: i for i, d in enumerate(dd)}
    out = []

    # A 50週平均
    sma = [None] * len(wcl)
    for i in range(49, len(wcl)):
        sma[i] = sum(wcl[i - 49:i + 1]) / 50
    sigA = [i for i in range(50, len(wcl))
            if abs(wcl[i] / sma[i] - 1) <= 0.02 and wcl[i - 1] > sma[i - 1] * 1.02]
    out.append(evaluate('A 50週平均(±2%、上から)', wd, wop, sigA, 50, [4, 13, 26], 13, 4))

    # B 恐怖・強欲指数 Extreme Fear
    fng = [r for r in csv.DictReader(open(os.path.join(D, 'fng.csv'))) if r['date_utc'] <= END]
    sigB, prev = [], None
    for r in fng:
        ef = r['classification'] == 'Extreme Fear'
        if ef and prev is False and r['date_utc'] in didx:
            sigB.append(didx[r['date_utc']])
        prev = ef
    out.append(evaluate('B 恐怖・強欲指数(Extreme Fear入り)', dd, dop, sorted(sigB), didx[fng[0]['date_utc']], [7, 30, 90], 30, 30))

    # C RSI14 が30を割る
    rsi = rsi_wilder(dcl)
    sigC = [i for i in range(15, len(dcl)) if rsi[i] < 30 <= rsi[i - 1]]
    out.append(evaluate('C RSI(14)が30割れ', dd, dop, sigC, 15, [7, 30, 90], 30, 30))

    # D 資金調達率がマイナスの日
    neg_days, first_fd = set(), None
    for r in csv.DictReader(open(os.path.join(D, 'funding.csv'))):
        day = r['time_utc'][:10]
        if day > END:
            continue
        first_fd = first_fd or day
        if float(r['funding_rate']) < 0:
            neg_days.add(day)
    sigD = sorted(didx[d] for d in neg_days if d in didx)
    out.append(evaluate('D 資金調達率がマイナスの日', dd, dop, sigD, didx[first_fd], [7, 30], 30, 30))

    # 表示
    for r in out:
        print(f"\n■ {r['name']}  条件成立 {r['raw_signals']}回 → 重複除き {r['events_all']}件")
        for h, x in r['horizons'].items():
            if not x['n']:
                print(f'  {h}: 件数0'); continue
            tag = f"  ← 判定: {x['verdict']}" if 'verdict' in x else ''
            print(f"  {h:>2}{'週' if r['name'].startswith('A') else '日'}: n={x['n']:>3} 平均 {x['mean']*100:+6.2f}% 中央 {x['median']*100:+6.2f}% 勝率 {x['win']*100:4.0f}% "
                  f"最悪 {x['worst']*100:+6.1f}% | ランダム平均 {x['random_mean']*100:+6.2f}% 勝率 {x['random_win']*100:4.0f}% | p={x['p']:.4f}{tag}")
        print(f"  期間 {r['horizons'][r['primary']]['period']}")
    json.dump(out, open('results.json', 'w'), ensure_ascii=False, indent=1, default=str)
    with open('events.csv', 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['signal', 'horizon', 'signal_bar', 'entry_bar', 'return_after_fee'])
        for r in out:
            for h, x in r['horizons'].items():
                for e in x['events']:
                    w.writerow([r['name'], h, *e])


if __name__ == '__main__':
    main()
