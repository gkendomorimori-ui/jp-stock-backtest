# Strategy Template

新しい戦略を追加するときの仕様テンプレート。
このファイルをコピーして `strategies/<strategy_name>.md` として作成する。

未確定の項目は空欄にせず `TODO` と書く。Claude Code は TODO の項目を独自判断で埋めない。

---

## Strategy Name

- name: `<snake_case_name>`（コード上の識別子）
- version: `0.1.0`
- author: 
- created: YYYY-MM-DD

## Hypothesis

なぜこの手法で利益が出ると考えるか。どの市場の非効率・行動バイアスを狙うか。
どのような結果が出たら仮説が否定されるか。

## Universe

- 市場：（例：東証プライム）
- 銘柄選定条件：
- 除外条件：
- Survivorship bias への対応：

## Timeframe

- 足種：日足
- シグナル判定タイミング：Day T 終値確定後
- 約定タイミング：Day T+1（始値 / 終値 / その他）

## Entry

- 条件：
- 約定価格：

## Exit

- 条件：
- 約定価格：
- 最大保有期間：

## Position Sizing

- 方式：（等金額 / 固定株数 / ボラティリティ調整 など）
- 単元株の考慮：

## Maximum Positions

- 同時保有銘柄数の上限：
- 上限を超えるシグナルが出た場合の優先順位：

## Stop Loss

- 条件：
- 同日に Take Profit と両方到達した場合の扱い：

## Take Profit

- 条件：

## Parameters

| name | default | 探索範囲（任意） | 説明 |
|---|---|---|---|
| | | | |

## Benchmark

- 比較対象：（例：TOPIX Buy & Hold）

## Test Period

- 全期間：
- In-sample / Out-of-sample の分割：
- ウォームアップ期間：

## Evaluation

- 必須指標：docs/BACKTEST_RULES.md の評価指標すべて
- 追加で確認すること：（年ごとの成績、銘柄別寄与、相場環境別、パラメータ感度 など）
- 合格基準（任意）：

## Notes

- 懸念点・既知の制約：
- 参考文献：
