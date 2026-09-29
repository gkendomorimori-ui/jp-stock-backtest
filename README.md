# jp-stock-backtest

日本株を対象に、複数の投資・トレード手法を **同一条件** でバックテストし比較するための検証基盤です。

「最も利益が出た戦略」を探すのではなく、**実運用可能性と再現性** を重視します。
リターンに加えて、ドローダウン・リスク・年ごとの安定性・銘柄依存性・相場環境依存性・パラメータ依存性を比較します。

> 現在のステータス：**動作確認の準備完了**。データ取得・加工・Universe・最初の戦略（[high_price_breakout](strategies/high_price_breakout.md)）・バックテストエンジンを実装済み（人工データでテスト済み）。実データでの動作確認（Free プラン）はこれからです。

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

## データ（J-Quants API V2）

株価データは J-Quants API V2 から取得する想定です（詳細は [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)）。

- API キーと取得したデータは **Git に含めません**（`.env`、`data/raw/`、`data/processed/` は `.gitignore` 済み）
- 過去5年の日足を使うには **Light プラン以上**が必要です。Free プランは約2年分で、12週間遅れです

### API キーの設定

1. J-Quants のダッシュボードで API キーを発行する
2. `.env.example` をコピーして `.env` を作り、キーを書く

```powershell
Copy-Item .env.example .env
notepad .env        # JQUANTS_API_KEY=発行したキー
```

### 接続確認（少量データ）

データ取得の本体はまだ実装されていません。`scripts/check_jquants_connection.py` で、API キーと接続、データの中身を少量のリクエストで確認できます。Free プランでも実行できます（Free プランのレート制限に合わせ、リクエストの間隔を13秒あけるので、数分かかります）。

```powershell
# 基本の確認（日付は Free プランなら12週間より前にする）
python scripts/check_jquants_connection.py --date 2026-06-01

# 上場廃止銘柄の確認：契約の取得可能期間内に上場廃止となった「既知の」銘柄の
# 5桁コードと、その銘柄がまだ上場していた日付を指定する
python scripts/check_jquants_connection.py --date 2026-06-01 --delisted-code <5桁コード> --listed-date <上場中の日付>

# 上場廃止銘柄と TOPIX だけを確認する（リクエスト数が少ない）
python scripts/check_jquants_connection.py --only delisted --date 2026-06-01 --delisted-code <5桁コード> --listed-date <上場中の日付>
```

レート制限（HTTP 429）を受けた場合は、自動で約1分待ってから再試行します。

確認する内容：

1. 取引カレンダー（営業日の判定）
2. 日足のサンプル（四本値・出来高・売買代金・調整後の値）
3. 銘柄マスタ：市場区分 × 商品区分の件数と、**分類保留**（普通株と確定できないもの）の件数・銘柄
4. 上場廃止銘柄：上場期間中の銘柄情報と株価が取れるか（比較で見つかった候補も表示する）
5. TOPIX

出力をそのまま貼ってもらえれば、商品区分の判定ルールと上場廃止銘柄の扱いを確定します。取得したレスポンスは `data/raw/jquants/connection_check/` に保存されます（Git 管理外）。

> **注意：** Free プランでの確認は**動作確認**です。5年分を使う正式な評価とは区別します。

### 上場廃止銘柄（Survivorship bias）について

J-Quants は、上場廃止銘柄についても上場していた期間のデータを返す仕様です。そのためバックテストでは、**各日時点で上場していた銘柄を対象にする（上場廃止銘柄も含める）**方針です。

- 既知の上場廃止銘柄（ネットワンシステムズ、2025-03-18 上場廃止）で、上場していた時点の銘柄情報と、最終売買日までの株価が取得できることを確認済みです（Free プランの範囲）
- 5年分を取得するときも、同じ確認を行います
- 確認できなかった場合、または取得できない期間がある場合は、**バックテスト結果が生き残った銘柄に偏る（成績が良く出やすい）制約**として、この README と各結果の `summary.json` に明記します
- 保有中の銘柄が上場廃止になった場合、初期版では**自動決済しません**。実行を「要確認」として止め、成績は未確定として扱います（[docs/BACKTEST_RULES.md](docs/BACKTEST_RULES.md)）

## 動作確認（Free プラン、約3か月）

Free プランで取得できる**最新日までの約3か月**を評価期間とし、その前に**20営業日のウォームアップ**を付けて実行します（「今日までの3か月」ではありません。Free プランのデータは約12週間遅れです）。

```powershell
python scripts/download_data.py --smoke     # 取得（約170リクエスト、40分前後。中断しても同じコマンドで再開）
python scripts/process_data.py --smoke      # 加工（data/processed/jquants/）
python scripts/run_backtest.py --smoke      # 実行（results/runs/<run_id>/）
python scripts/verify_run.py                # 保存された結果の整合性チェック（利益の良し悪しは見ない）
python scripts/compare_runs.py results/runs/<前回のrun_id>   # 最新の実行と売買結果が同一か比較
```

- 期間は最初の実行時に決まり、`data/raw/jquants/smoke_period.json` に保存されます。再開しても期間は変わりません（決め直すときは `--redetermine`）
- **成功条件**：取得・加工・実行・保存がエラーなく完了すること。**利益の大小は成功条件にしません**。結果を見て戦略のパラメータを変えることもしません
- 結果は `run_type: smoke_test` として保存され、正式な評価とは区別されます。TOPIX は Free プランでは取れないので、ベンチマークの数値は `null` で、未取得の理由が別の項目に記録されます

動作確認の記録：[docs/smoke_tests/](docs/smoke_tests/)（最新：[2026-09-28 high_price_breakout Free プラン](docs/smoke_tests/2026-09-28_high_price_breakout_free_plan.md)）

出力されるファイル（`results/runs/<run_id>/`）：

| ファイル | 内容 |
|---|---|
| `summary.json` | 設定・期間・評価指標・件数の集計・注記・ベンチマーク（未取得の理由） |
| `trades.csv` | 取引履歴 |
| `equity_curve.csv` | 日ごとの資産推移（現金・保有評価額） |
| `orders.csv` | 買い注文の一覧（約定・取消とその理由） |
| `unresolved_events.csv` | 要確認の事象（上場廃止など）があった場合のみ |

## 5年分のデータ（Light プラン）

期間の分け方は [docs/EVALUATION_PLAN.md](docs/EVALUATION_PLAN.md) で確定済み（開発用 2021-10-27〜2024-04-02、最終評価用 2024-04-03〜2026-04-02 は**未閲覧・実行不可**）。

```powershell
python scripts/download_data.py --start 2021-09-29 --end 2026-09-28 --min-interval 1.1   # 古い日から順に取得（最後に TOPIX も取得）
python scripts/process_data.py --start 2021-09-29 --end 2026-09-28
python scripts/check_periods.py                   # 取引所カレンダーで区間の営業日数を照合
python scripts/check_data_quality.py              # 全期間のデータ品質確認（戦略の成績は計算しない）
python scripts/run_backtest.py --period development
python scripts/verify_run.py                      # 整合性チェック
python scripts/breakdown_trades.py                # 補助的な内訳（10円未満 / +100%超 / どちらでもない）
```

`run_backtest.py` は、最終評価用・追加確認用の区間や、区間をまたぐ期間の実行を拒否する。

## 検証期間とベンチマーク

> 期間の分け方、閲覧済みの期間、感度分析の条件は [docs/EVALUATION_PLAN.md](docs/EVALUATION_PLAN.md) で管理する（成績を見る前に固定）。

- 5年分のデータを確保できた場合、古い約3年を**開発用**、新しい約2年を**最終評価用**とします。境界日はデータ取得後、戦略の成績を見る前に固定します
- **最終評価用期間の結果を見てパラメータを調整しません**
- 開発用・最終評価用は、それぞれ150万円・保有なしから開始します
- データの先頭20営業日はウォームアップ（指標の計算）に使うので、実際に成績を評価できる期間はその分短くなります
- ベンチマークは **TOPIX（価格指数、配当込みではない）**。J-Quants では **Light プラン以上**でないと取得できません。戦略側も初期版では配当を計上しないので、「どちらも配当なし」の条件で比較します

## 出力形式

各実験は `results/runs/<run_id>/` に以下を保存します（詳細は [docs/BACKTEST_RULES.md](docs/BACKTEST_RULES.md)）。

- `summary.json` — 評価指標と再現用メタデータ
- `trades.csv` — 取引履歴
- `equity_curve.csv` — 資産推移
