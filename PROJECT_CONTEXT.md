# Project Context

## 目的

日本株を対象として複数の投資・トレード手法を同一条件でバックテストし、リターンだけでなくドローダウンや安定性を含めて比較する。

最終的には実運用可能なシステムトレード戦略の構築を目指す。

## 基本方針

- 対象：日本株
- 主軸：日足
- 複数戦略を同一基盤で比較
- 2年・5年など検証期間を変更可能にする
- データ取得部分とバックテスト部分を分離
- データソースを後から交換可能にする
- 売買手数料を考慮
- スリッページを考慮
- 未来情報を使用しない
- 再現可能なバックテストを重視

## 評価指標

最低限以下を出力する。

- Total Return
- CAGR
- Maximum Drawdown
- Sharpe Ratio
- Sortino Ratio
- Profit Factor
- Win Rate
- Number of Trades
- Average Profit
- Average Loss
- Expectancy

## 重要な評価方針

利益最大化だけを目的としない。

特に、

- 最大ドローダウン
- 年ごとの安定性
- 銘柄依存性
- 相場環境依存性
- パラメータ依存性

を確認する。

過剰最適化を避ける。

## 関連ドキュメント

- [README.md](README.md) — 全体像・アーキテクチャ・開発ループ
- [CLAUDE.md](CLAUDE.md) — 実装担当（Claude Code）向けの開発ルール
- [docs/BACKTEST_RULES.md](docs/BACKTEST_RULES.md) — 全戦略共通のバックテストルール
- [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) — データソースの分離方針と比較表
- [docs/STRATEGY_TEMPLATE.md](docs/STRATEGY_TEMPLATE.md) — 戦略仕様テンプレート
