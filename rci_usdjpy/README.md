# 手法の通信簿：3本RCIの押し目買い（ドル円4時間足）

YouTube で「10年10か月・239回・勝率50.63%・PF 1.45・100万円が約328万円」と紹介されていた手法を、ルールを数字で固定して機械的に回した採点です。

- [PREREG.md](PREREG.md) — 事前固定（データを見る前に確定）
- [RESULTS.md](RESULTS.md) — 結果。型1（主）は**再現せず**（56回・PF 0.97・p = 0.57）、型2は件数不足
- [EXPLORE_pullback.md](EXPLORE_pullback.md) — 結果を見たあとの探索（損切りを押しの底に置いた版）。PF 0.83 で追わない
- `score_main.py` — 主な採点に使ったスクリプト（`results_main.json` を出した版）
- `score_v2.py` — 損切りの置き方を選べるようにした版。`last20` で `results_main.json` と同じ結果、`pullback` で `results_pullback.json` を出す
- `results_main.json` / `results_pullback.json` — 全集計と、スプレッド1.6 pipsの全取引

## データ
生データ（Dukascopy の4時間足）は含めない。次のコマンドで取り直せる（Node.js が必要）。

```
npx dukascopy-node -i usdjpy -from 2009-09-01 -to 2026-11-01 -t h4 -p bid -f csv -df iso -dir rci_data -fn usdjpy-h4-bid
python3 score_main.py rci_data/usdjpy-h4-bid.csv results_main.json
python3 score_v2.py rci_data/usdjpy-h4-bid.csv results_pullback.json pullback
```

取得したファイル（2026-11-13 取得）：37,410行、2009-09-01〜2026-10-30 20:00 UTC。値動きのない足 9,732本を除いた 27,678本を使用。

| ファイル | SHA-256 |
|---|---|
| usdjpy-h4-bid.csv（取得したデータ、含めない） | 0a5c5ab28a3fb9f3f790c5089e1cd05e6c8ff8f52c0c9da96119d3d20360eef4 |
| score_main.py | 89e4925e796895be832feed75736d459189d25265e242cd777381c5ace572202 |
| score_v2.py | 0d7dc4010ad15cc64908ca1e7362dcbe4cc27a3bce043a504d3e64dba76c9a6f |
| results_main.json | eb8056b1f4fb1139459fd47e2fcd161b29767ac1b6b2bfef8284009bdc3fdbc0 |
| results_pullback.json | 2e24a50c566000cc4ebdb5b926710482b619331d1979bce9a405686cb06134d6 |

Dukascopy の配信データは、取得の時期によって細部が変わることがある。取り直したデータのハッシュが上と違う場合は、結果も少し変わりうる。

## 公開の時期について
事前固定は 2026-11-13 07:43 JST に非公開の作業記録の中で確定し、データ取得・計算はその後に行った。このリポジトリへの公開は結果と同時で、計算の前の公開と時刻証明は取っていない。
