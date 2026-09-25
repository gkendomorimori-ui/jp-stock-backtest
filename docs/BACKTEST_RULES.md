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

## Output

各実験について `results/runs/<run_id>/` に最低限、

- `summary.json` — 評価指標 + 上記メタデータ
- `trades.csv` — 取引履歴
- `equity_curve.csv` — 日次の資産推移

を保存する。

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

## バイアス対策チェックリスト

- [ ] シグナル計算に Day T より後のデータを使っていない
- [ ] 約定は Day T+1 以降
- [ ] 指標のウォームアップ期間を検証期間から除外している
- [ ] 上場廃止銘柄を含むかどうか（Survivorship bias）を結果に明記している
- [ ] コスト（手数料・スリッページ）を適用している
- [ ] パラメータ探索を行った場合、探索期間と評価期間を分けている
