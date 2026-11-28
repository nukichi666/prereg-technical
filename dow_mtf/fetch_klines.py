import sys, os, time, json, urllib.request, urllib.error, datetime

# ==========================================================
# 長い期間の1分足を取って保存するだけ(読み取り専用・発注なし・鍵はいらない)。
# Binance の無期限先物(USDT)の1分足。HYPE、BTC、ETH、SOL。取れるところまで全部取る。
# 使い方: python fetch_klines.py klines_data
#          python fetch_klines.py klines_data HYPEUSDT,BTCUSDT     (銘柄を指定)
# 途中で止まっても、もう一度同じコマンドで続きから取る(取れたところまでは保存済み)。
# 出力: klines_data/HYPEUSDT_1m.csv など(open_time_ms, open, high, low, close, volume)
# ==========================================================
BASE = "https://fapi.binance.com"
DEFAULT = ["HYPEUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
LIMIT = 1500
MIN_MS = 60 * 1000


def get_json(url, tries=6):
    wait = 3.0
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                time.sleep(wait)
                wait = min(60.0, wait * 2)
                continue
            raise
        except Exception:
            time.sleep(wait)
            wait = min(60.0, wait * 2)
    raise RuntimeError("取得に失敗: " + url)


def last_time(path):
    if not os.path.exists(path):
        return None
    last = None
    with open(path, "rb") as f:
        try:
            f.seek(-4096, 2)
        except OSError:
            f.seek(0)
        lines = f.read().decode(errors="ignore").strip().splitlines()
    for ln in reversed(lines):
        p = ln.split(",")
        if len(p) >= 6 and p[0].isdigit():
            last = int(p[0])
            break
    return last


def fetch(sym, folder):
    path = os.path.join(folder, "%s_1m.csv" % sym)
    lt = last_time(path)
    start = (lt + MIN_MS) if lt is not None else 0
    now_ms = int(time.time() * 1000)
    new = not os.path.exists(path)
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        if new:
            f.write("open_time,open,high,low,close,volume\n")
        while start < now_ms - MIN_MS:
            rows = get_json("%s/fapi/v1/klines?symbol=%s&interval=1m&startTime=%d&limit=%d" % (BASE, sym, start, LIMIT))
            if not rows:
                break
            for r in rows:
                if int(r[6]) >= now_ms:   # まだ確定してない足は入れない
                    continue
                f.write("%d,%s,%s,%s,%s,%s\n" % (int(r[0]), r[1], r[2], r[3], r[4], r[5]))
                n += 1
            nxt = int(rows[-1][0]) + MIN_MS
            if nxt <= start:
                break
            start = nxt
            f.flush()
            if n and (n // LIMIT) % 20 == 0:
                print("   %s: %d本 (%s まで)" % (sym, n, datetime.datetime.fromtimestamp(start / 1000).strftime("%Y-%m-%d %H:%M")))
            time.sleep(0.25)
            if len(rows) < LIMIT and start >= now_ms - 2 * MIN_MS:
                break
    return path, n


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "klines_data"
    syms = sys.argv[2].split(",") if len(sys.argv) > 2 else DEFAULT
    os.makedirs(folder, exist_ok=True)
    for s in syms:
        try:
            path, n = fetch(s, folder)
        except Exception as e:
            print("%s: 失敗 %s" % (s, repr(e)[:200]))
            continue
        first = last = None
        cnt = 0
        with open(path, encoding="utf-8") as f:
            next(f, None)
            for ln in f:
                p = ln.split(",", 1)
                if p[0].isdigit():
                    t = int(p[0])
                    first = t if first is None else first
                    last = t
                    cnt += 1
        if cnt:
            fm = lambda t: datetime.datetime.fromtimestamp(t / 1000).strftime("%Y-%m-%d %H:%M")
            print("%s: 追加 %d本 / 合計 %d本 / %s 〜 %s (%.0f日)" % (s, n, cnt, fm(first), fm(last), (last - first) / 86400000.0))
        else:
            print("%s: データなし" % s)
    print("終わり。次: python dow_search.py %s" % folder)


if __name__ == "__main__":
    main()
