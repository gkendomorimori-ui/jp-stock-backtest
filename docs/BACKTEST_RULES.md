# Backtest Rules

全戦略共通のバックテストルール。個別戦略の仕様書はこのルールを前提とし、例外がある場合は仕様書に明記する。

## Execution

日足データを基本とする。

シグナル生成に当日の終値を利用した場合、原則として同じ終値で約定したことにはしない。

基本：

```
Signal at Day T   （Day T の終値までの情報で判定）
  ↓
Execution at Day T+1
```

具体的な約定条件（T+1 の始値／終値／指値など）は各戦略仕様で定義する。

補足ルール：

- Day T+1 に取引がない（売買停止・休場・データ欠損）場合の扱いは戦略仕様で定義する。未定義の場合は TODO として報告する
- 当日の高値・安値を使った損切り・利確（日中約定）を想定する場合は、同一日に損切りと利確の両方に到達した際の優先順位を仕様で定義する
- 株式分割・併合は調整後株価で扱うか、元データと調整係数で扱うかをデータ層で統一する（docs/DATA_SOURCES.md）

## Costs

以下を設定値として変更可能にする（`config/backtest.yaml`）。

- Commission（売買手数料）
- Slippage（スリッページ）

コストは売買の双方向に適用する。値はコード中にハードコードしない。

## Position

戦略ごとに以下を定義する。

- Entry
- Exit
- Position Size
- Maximum Positions
- Stop Loss
- Take Profit

その他、共通で留意する点：

- 売買単位（単元株）を考慮するかどうか
- 空売り（信用売り）を扱うかどうか、および貸株料等のコスト
- 資金不足時の扱い

これらも戦略仕様または共通設定で定義する。

## Reproducibility

バックテスト実行時に最低限以下を保存する。

- strategy name
- strategy version
- parameters
- universe
- test period
- commission
- slippage
- execution timestamp
- Git commit hash（取得可能な場合）

乱数を使う場合は seed も保存する。

あわせて以下も保存する。

- run_type（`smoke_test` / `development` / `final_evaluation`）
- status（`complete` / `needs_review`）
- dividends_included（配当を計上したか）
- benchmark（比較に使った指数）

## Output

各実験について `results/runs/<run_id>/` に最低限、

- `summary.json` — 評価指標 + 上記メタデータ
- `trades.csv` — 取引履歴
- `equity_curve.csv` — 日次の資産推移

を保存する。`status = needs_review` の場合は `unresolved_events.csv` も保存する。

### trades.csv（暫定カラム）

| column | 説明 |
|---|---|
| symbol | 銘柄コード |
| side | long / short |
| entry_date | 約定日（エントリー） |
| entry_price | 約定価格（スリッページ込み） |
| exit_date | 約定日（イグジット） |
| exit_price | 約定価格（スリッページ込み） |
| quantity | 株数 |
| commission | 往復手数料 |
| pnl | 損益（コスト控除後） |
| return_pct | 損益率 |
| exit_reason | signal / stop_loss / take_profit / end_of_test など |

### equity_curve.csv（暫定カラム）

| column | 説明 |
|---|---|
| date | 日付 |
| equity | 評価額合計 |
| cash | 現金 |
| position_value | ポジション評価額 |

## 評価指標

最低限以下を `summary.json` に出力する（定義は `src/evaluation/metrics.py`）。

Total Return / CAGR / Maximum Drawdown / Sharpe Ratio / Sortino Ratio / Profit Factor / Win Rate / Number of Trades / Average Profit / Average Loss / Expectancy

年率換算の日数・無リスク金利は設定値とする（TODO: 値を決定）。

## データ欠損と上場廃止

「データがない」と「上場廃止」は区別する。

| 状況 | 判定方法 | 扱い |
|---|---|---|
| データ欠損・売買不成立 | 銘柄マスタには上場しているが、その日の四本値などが null | その日は約定できない。保有中なら持ち越し、評価額は直前の有効な終値で計算する。新規購入の候補にはしない（条件は各戦略仕様） |
| 上場廃止 | その日の銘柄マスタ（`/v2/equities/master?date=D`）に銘柄が存在しない | 下記 |

### 保有中の銘柄が上場廃止になった場合（初期版）

- 売却・現金交付・株式交換などの処理を確定できない場合は、**自動決済しない**
  - 「取得できた最後の終値で決済」はしない
  - 架空の売却代金を現金に戻さない
  - 該当する銘柄や取引を、黙って結果から除外しない
- その時点で実行を止め、**「要確認」**として記録する
  - `summary.json` の `status` を `needs_review` にし、成績は**未確定**と明記する
  - `unresolved_events.csv` に、日付・銘柄・保有株数・購入価格・最後の有効な終値と日付・理由を記録する
  - 止まった日の前日までの結果（trades.csv、equity_curve.csv）は保存してよい
- 対象事例を確認してから、処理を追加する

## 実行の種類（run_type）

| run_type | 内容 | 成績の扱い |
|---|---|---|
| `smoke_test` | Free プランなどの少量データでの動作確認 | 評価には使わない |
| `development` | 開発用期間での検証 | 仕様・パラメータの検討に使ってよい |
| `final_evaluation` | 最終評価用期間での検証 | 最終評価だけに使う。この結果でパラメータを調整しない |

`summary.json` には `run_type` を必ず記録する。

## 期間の分割

5年分のデータを確保できた場合：

- 古い約3年を**開発用**、新しい約2年を**最終評価用**とする
- 正確な境界日は、データ取得後、**戦略の成績を見る前に**固定し、`config/backtest.yaml` に記録する
- 開発用・最終評価用は、それぞれ**初期資金・保有なし**から開始する（開発用の最終日に保有していたポジションは、最終評価用に引き継がない）
- 最終評価の開始日より前の20営業日は、指標計算にだけ使う。売買や成績には含めない
- データの先頭20営業日をウォームアップに使う場合、実際に成績を評価できる期間はその分短くなる

## ベンチマーク

- 初期ベンチマークは **TOPIX（価格指数）**。配当込み指数ではない
- 同じ評価期間の初日の値を、戦略の初期資産にそろえて比較する
- 戦略側で配当を計上するかどうかを `summary.json`（`dividends_included`）に記録し、比較条件を区別する。初期版は配当を計上しない

## バイアス対策チェックリスト

- [ ] シグナル計算に Day T より後のデータを使っていない
- [ ] 約定は Day T+1 以降
- [ ] 指標のウォームアップ期間を検証期間から除外している
- [ ] 上場廃止銘柄を含むかどうか（Survivorship bias）を結果に明記している
- [ ] コスト（手数料・スリッページ）を適用している
- [ ] パラメータ探索を行った場合、探索期間と評価期間を分けている
- [ ] 最終評価用期間の結果を見てパラメータを調整していない
- [ ] 上場廃止を最後の終値で自動決済していない（要確認として止めている）
- [ ] `run_type` と `status` が summary.json に記録されている
