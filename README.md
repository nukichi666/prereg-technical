# prereg-technical

テクニカル平均回帰フィルターの事前登録研究。

**最新の報告：[フェーズAの結果](reports/phase_a.md)（主要3対比すべて支持されず）**

- フェーズB（前向き追跡）：2026-11-01〜2027-01-31。台帳は `signals/`、実行記録は `run_log.csv`

- `prereg_v1.md` — 事前登録書（主要対比 P2-L / P2-U / P3-HV、検定方法、フェーズA/B）
- `candidates_v0.2.1.md` — 指標・イベント・発火規則の定義（`candidates_v0.md` は同一内容）
- `*.ots` — OpenTimestamps による時刻証明（検証: https://opentimestamps.org ）
- `CHANGELOG.md` — すべての変更と、その時点での成績閲覧の有無
- `resample.py` / `screen_frequency.py` — データ処理と頻度スクリーナー
- `frequency_v0.2.1.csv` / `filters_v0.2.1.csv` — 主要対比の選定に使った頻度結果（成績は含まない）

生データ（Dukascopy / Binance の1分足）は容量のため含めない。`fetch_data.ps1` で再取得できる。

| ファイル | SHA-256 |
|---|---|
| prereg_v1.md | 8d7eddfedfbee336e7d96302d3269477993b1bca95cda4631c5d6552b26107e1 |
| candidates_v0.2.1.md | 05a204922d1f6ea18da81703372c70f9cf7a8caa057c7cc0bdc8281098aaf131 |
