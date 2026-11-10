"""BTC サイン採点のデータを取ってくる(Mac で1回だけ実行。標準ライブラリだけで動く)

  python3 fetch_data.py

保存先: このファイルと同じフォルダの data/
  btc_1d.csv       Binance 現物 BTCUSDT 日足(2017-08-17〜)
  btc_1w.csv       Binance 現物 BTCUSDT 週足
  fng.csv          alternative.me 恐怖・強欲指数(2018-02-01〜)
  funding.csv      Binance 先物 BTCUSDT 資金調達率(2019-09〜)
鍵は使わない(公開データだけ)。
"""
import csv, json, os, time, urllib.request, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'data')
os.makedirs(OUT, exist_ok=True)
UA = {'User-Agent': 'btc-signals-research/1.0'}


def get(url):
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:
            print(f'  やり直し {i + 1}/5: {e}')
            time.sleep(3 * (i + 1))
    raise SystemExit(f'取れなかった: {url}')


def klines(interval, path):
    rows, start = [], 1500000000000          # 2017-07 から(Binance は 2017-08-17 が最初)
    while True:
        j = get(f'https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval={interval}&startTime={start}&limit=1000')
        if not j:
            break
        rows += j
        start = j[-1][0] + 1
        if len(j) < 1000:
            break
        time.sleep(0.3)
    now = int(time.time() * 1000)
    rows = [r for r in rows if r[6] < now]   # まだ終わっていない足は入れない
    with open(path, 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['open_time_utc', 'open', 'high', 'low', 'close', 'volume'])
        for r in rows:
            t = datetime.datetime.fromtimestamp(r[0] / 1000, datetime.timezone.utc).strftime('%Y-%m-%d')
            w.writerow([t, r[1], r[2], r[3], r[4], r[5]])
    d = lambda ms: datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime('%Y-%m-%d')
    print(f'  {os.path.basename(path)}: {len(rows)}本 {d(rows[0][0])} 〜 {d(rows[-1][0])}')


def fng(path):
    j = get('https://api.alternative.me/fng/?limit=0&format=json')['data']
    rows = sorted(j, key=lambda d: int(d['timestamp']))
    with open(path, 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['date_utc', 'value', 'classification'])
        for d in rows:
            t = datetime.datetime.fromtimestamp(int(d['timestamp']), datetime.timezone.utc).strftime('%Y-%m-%d')
            w.writerow([t, d['value'], d['value_classification']])
    first = datetime.datetime.fromtimestamp(int(rows[0]['timestamp']), datetime.timezone.utc).strftime('%Y-%m-%d')
    print(f'  fng.csv: {len(rows)}日 {first} 〜')


def funding(path):
    rows, start = [], 1567296000000          # 2019-09-01
    while True:
        j = get(f'https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&startTime={start}&limit=1000')
        if not j:
            break
        rows += j
        start = j[-1]['fundingTime'] + 1
        if len(j) < 1000:
            break
        time.sleep(0.3)
    with open(path, 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['time_utc', 'funding_rate'])
        for d in rows:
            t = datetime.datetime.fromtimestamp(d['fundingTime'] / 1000, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M')
            w.writerow([t, d['fundingRate']])
    print(f'  funding.csv: {len(rows)}件')


if __name__ == '__main__':
    print('== 日足'); klines('1d', os.path.join(OUT, 'btc_1d.csv'))
    print('== 週足'); klines('1w', os.path.join(OUT, 'btc_1w.csv'))
    print('== 恐怖・強欲指数'); fng(os.path.join(OUT, 'fng.csv'))
    print('== 資金調達率'); funding(os.path.join(OUT, 'funding.csv'))
    print('完了。data/ に4つ保存した')
