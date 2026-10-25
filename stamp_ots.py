"""
OpenTimestamps の .ots ファイルを作る最小実装（ots コマンドの代わり）

- 公式クライアントと同じ構造：ファイルの SHA-256 → 16バイトのノンスを付けて SHA-256 →
  その値を複数のカレンダーサーバーに送り、返ってきた証明を分岐（fork）としてまとめる
- 作られる .ots は「受付済み（pending）」の状態。ビットコインに刻まれた後は
  https://opentimestamps.org にファイルと .ots を入れると完成版に更新・検証できる

使い方: python stamp_ots.py FILE [FILE ...]
"""
import hashlib
import os
import sys
import urllib.request

MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"
CALENDARS = [
    "https://alice.btc.calendar.opentimestamps.org",
    "https://bob.btc.calendar.opentimestamps.org",
    "https://finney.calendar.eternitywall.com",
]
PENDING_TAG = bytes.fromhex("83dfe30d2ef90c8e")


def submit(url, digest):
    req = urllib.request.Request(url + "/digest", data=digest, method="POST",
                                 headers={"Accept": "application/vnd.opentimestamps.v1",
                                          "User-Agent": "prereg-technical-stamp"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def stamp(path):
    file_digest = hashlib.sha256(open(path, "rb").read()).digest()
    nonce = os.urandom(16)
    merkle = hashlib.sha256(file_digest + nonce).digest()
    resps = []
    for c in CALENDARS:
        try:
            resp = submit(c, merkle)
            if PENDING_TAG in resp:
                resps.append(resp)
        except Exception as e:
            print(f"  {c}: 失敗 ({e})")
    if not resps:
        raise RuntimeError("どのカレンダーにも登録できませんでした")
    body = b"\xf0" + bytes([len(nonce)]) + nonce + b"\x08"       # append(nonce), sha256
    for r in resps[:-1]:
        body += b"\xff" + r                                       # fork
    body += resps[-1]
    with open(path + ".ots", "wb") as f:
        f.write(MAGIC + b"\x01" + b"\x08" + file_digest + body)
    print(f"{path}.ots 作成（カレンダー {len(resps)}/{len(CALENDARS)}）")


if __name__ == "__main__":
    ok = True
    for p in sys.argv[1:]:
        try:
            stamp(p)
        except Exception as e:
            ok = False
            print(f"{p}: {e}")
    sys.exit(0 if ok else 1)
