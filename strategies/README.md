# strategies/

戦略 **仕様書** の置き場（Markdown）。実装コードは `src/strategies/` に置く。

## 追加手順

1. `docs/STRATEGY_TEMPLATE.md` をコピーして `strategies/<strategy_name>.md` を作成（主に ChatGPT 側）
2. 必要ならパラメータを `strategies/<strategy_name>.yaml` に定義
3. Claude Code が `src/strategies/<strategy_name>.py` に `Strategy` のサブクラスとして実装
4. `tests/` にテストを追加
5. `scripts/run_backtest.py` で実行 → `results/runs/<run_id>/`

## 命名規則

- ファイル名・`Strategy.name` は snake_case で一致させる
- 仕様を変更したら `version` を上げる（結果の `summary.json` に記録される）

## 登録済み戦略

| name | version | 概要 | 状態 |
|---|---|---|---|
| [high_price_breakout](high_price_breakout.md) | 0.1.0 | 20日高値の終値ブレイク＋出来高2倍。翌日寄付で買い、+10%利確／−5%損切り／最大20営業日 | 仕様確定・未実装 |
