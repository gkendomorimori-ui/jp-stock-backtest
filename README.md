# jp-stock-backtest

日本株を対象に、複数の投資・トレード手法を **同一条件** でバックテストし比較するための検証基盤です。

「最も利益が出た戦略」を探すのではなく、**実運用可能性と再現性** を重視します。
リターンに加えて、ドローダウン・リスク・年ごとの安定性・銘柄依存性・相場環境依存性・パラメータ依存性を比較します。

> 現在のステータス：**骨格のみ**。データ取得・具体的な戦略・本格的なバックテストエンジンは未実装です。

---

## Architecture

```
Data                 (src/data)        データ取得・正規化（Provider を差し替え可能）
  ↓
Indicators           (src/indicators)  テクニカル指標など（過去データのみで計算）
  ↓
Strategy             (src/strategies)  シグナル生成（パラメータは外部から注入）
  ↓
Backtest Engine      (src/backtest)    約定（T+1）・コスト・ポジション管理
  ↓
Evaluation           (src/evaluation)  評価指標・結果保存
  ↓
Strategy Comparison  (scripts/compare_strategies.py, results/comparison)
```

各レイヤーは可能な限り疎結合にします。

- レイヤー間は **正規化された DataFrame / dataclass** でやり取りし、下位レイヤーの実装詳細に依存しない
- 戦略は「データの取得元」を知らない（`DataProvider` の差し替えで戦略コードは変わらない）
- バックテストエンジンは「どの戦略か」を知らない（`Strategy` インターフェースのみに依存）
- 評価は「どのエンジンで計算したか」を知らない（資産推移と取引履歴のみに依存）

## Directory Structure

```
jp-stock-backtest/
├── README.md               このファイル
├── CLAUDE.md               Claude Code 向け開発ルール
├── PROJECT_CONTEXT.md      目的・基本方針・評価方針
├── pyproject.toml          依存関係・ツール設定
├── config/
│   ├── backtest.yaml       資金・コスト・期間などの共通設定（仮設定あり）
│   └── universe.yaml       対象市場・対象銘柄
├── docs/
│   ├── BACKTEST_RULES.md   全戦略共通のバックテストルール
│   ├── DATA_SOURCES.md     データソース分離方針・比較表
│   └── STRATEGY_TEMPLATE.md 戦略仕様テンプレート
├── src/
│   ├── data/               DataProvider インターフェース・OHLCV 正規化
│   ├── indicators/         指標計算
│   ├── strategies/         Strategy 基底クラス・各戦略実装
│   ├── backtest/           設定・コストモデル・エンジン（インターフェースのみ）
│   ├── evaluation/         評価指標・結果保存
│   └── utils/              設定読み込み・Git 情報など
├── strategies/             戦略仕様書（Markdown）
├── data/raw, data/processed   データ置き場（Git 管理外）
├── results/runs            各実験の結果（Git 管理外）
├── results/comparison      戦略比較結果（Git 管理対象）
├── tests/                  pytest
└── scripts/                実行用スクリプト（雛形）
```

## AI の役割分担

| 担当 | 主な役割 |
|---|---|
| **ChatGPT** | 投資戦略のアイデア、仮説設計、バックテスト条件設計、戦略仕様作成、結果分析、戦略比較、改善案作成、コードレビュー |
| **Claude Code** | Python 実装、テスト、リファクタリング、ファイル操作、バックテスト実行、Git 操作、バグ修正 |
| **GitHub** | コード・仕様・検証結果を管理する Single Source of Truth |

## 開発ループ

```
ChatGPT で仮説作成
  ↓
戦略仕様作成（strategies/<name>.md, docs/STRATEGY_TEMPLATE.md に準拠）
  ↓
Claude Code で実装（src/strategies/）
  ↓
バックテスト（scripts/run_backtest.py）
  ↓
結果保存（results/runs/<run_id>/）
  ↓
ChatGPT で分析
  ↓
改善仮説作成
  ↓
再検証
```

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"

pytest          # テスト
ruff check .    # lint
ruff format .   # format
mypy src        # 型チェック
```

## 出力形式

各実験は `results/runs/<run_id>/` に以下を保存します（詳細は [docs/BACKTEST_RULES.md](docs/BACKTEST_RULES.md)）。

- `summary.json` — 評価指標と再現用メタデータ
- `trades.csv` — 取引履歴
- `equity_curve.csv` — 資産推移
