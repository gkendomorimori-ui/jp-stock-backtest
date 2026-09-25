# Data Sources

## 方針

データソースをバックテストロジックから分離する。

```
Data Provider        （データソースごとの実装：src/data/providers/）
  ↓
Normalized OHLCV     （共通フォーマット：src/data/schema.py）
  ↓
Backtest Engine
```

- 各データソースは `DataProvider` インターフェース（`src/data/base.py`）を実装する
- Provider は取得したデータを **Normalized OHLCV** に変換して返す
- 戦略・エンジンは Normalized OHLCV のみに依存する
- データソースを変更しても戦略コードを変更する必要がない構造とする

**現時点では特定のデータソースに固定しない。**

## Normalized OHLCV（共通フォーマット）

long 形式（1 行 = 1 銘柄 × 1 日）とする。

| column | 型 | 説明 |
|---|---|---|
| date | datetime64 (tz-naive, JST 営業日) | 取引日 |
| symbol | str | 銘柄コード（例：`7203`）。表記は TODO で統一 |
| open | float | 始値 |
| high | float | 高値 |
| low | float | 安値 |
| close | float | 終値 |
| volume | float | 出来高 |

- 価格は **調整後か否かを Provider 単位で明示** する（メタデータ `adjusted: bool`）
- 欠損・異常値の扱いは `validate_ohlcv()` でチェックする
- TODO: 調整係数（分割・併合）を別カラムで持つかを決定する

## データソース比較表

候補の比較は以下の観点で行う。**値は未調査（TODO）**。調査後に埋める。

| 観点 | 候補A (TODO) | 候補B (TODO) | 候補C (TODO) |
|---|---|---|---|
| 取得可能期間 | | | |
| OHLCV の有無 | | | |
| 調整後株価 | | | |
| 上場廃止銘柄の有無 | | | |
| API 制限（レート・件数） | | | |
| 利用条件・ライセンス・費用 | | | |
| データ品質（欠損・誤り・更新遅延） | | | |
| 取得方法（API / CSV / スクレイピング） | | | |
| 備考 | | | |

### 観点の説明

- **取得可能期間**：2 年・5 年・それ以上の検証に足りるか
- **OHLCV**：始値・高値・安値・終値・出来高がすべて揃うか
- **調整後株価**：分割・併合の調整有無、調整方法
- **上場廃止銘柄**：Survivorship bias 回避に必要
- **API 制限**：取得速度・1 日あたりの上限
- **利用条件**：商用利用可否、再配布可否（リポジトリにデータを含めない前提）
- **データ品質**：欠損・誤データの頻度、修正履歴

## 認証情報

API キー等は `.env` に保存し、Git にコミットしない（`.gitignore` 済み）。
