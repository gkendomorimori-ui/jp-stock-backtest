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

**第一候補：J-Quants API V2**（2026-09-27 決定）。上記の分離方針は維持し、他のソースへ差し替えられる構造を保つ。

## データの保存場所

| 場所 | 内容 | Git |
|---|---|---|
| `data/raw/jquants/` | API レスポンスをそのまま保存（JSON / Parquet） | 管理外 |
| `data/processed/` | Normalized OHLCV などの加工済みデータ | 管理外 |
| `.env` | `JQUANTS_API_KEY` | 管理外（`.env.example` だけコミットする） |

- 元データ（raw）は加工せずに残し、加工はいつでも raw から作り直せるようにする
- J-Quants のデータは再配布できないので、リポジトリには含めない

## J-Quants API V2

| 項目 | 内容 |
|---|---|
| ベース URL | `https://api.jquants.com/v2/` |
| 認証 | ダッシュボードで発行した API キーを `x-api-key` ヘッダで送る（V1 のトークン方式は使わない） |
| 日足 | `GET /v2/equities/bars/daily`（`code` または `date` が必須。`from` / `to` で期間指定） |
| 銘柄マスタ | `GET /v2/equities/master`（`date` を指定すると、その日時点の上場銘柄を返す） |
| ページング | レスポンスの `pagination_key` を次のリクエストに渡す |
| レスポンス | `{"data": [...], "pagination_key": ...}` |

### 日足のフィールド

| フィールド | 意味 | 用途 |
|---|---|---|
| `Date`, `Code` | 日付、銘柄コード（5桁。例：`72030`） | キー |
| `O`, `H`, `L`, `C` | 未調整の四本値 | 約定・資金計算・資産評価 |
| `Vo` | 未調整の出来高 | — |
| `Va` | 売買代金（円） | 流動性フィルタ |
| `AdjO`, `AdjH`, `AdjL`, `AdjC`, `AdjVo` | 分割・併合を調整した値 | シグナル計算 |
| `AdjFactor` | 調整係数。権利落ち日に値が入る（1:2 分割なら `0.5`） | 保有株数・価格の調整 |
| `UL`, `LL` | ストップ高・ストップ安のフラグ | 今は使わない |

- 取引が成立しなかった日（無約定・終日売買停止など）は、四本値・出来高・売買代金が **null** になる。バックテストではこの日を「約定不可」として扱う
- 上場廃止銘柄も、データ格納期間内であれば上場していた期間のデータを取得できる

### 銘柄マスタのフィールド

`Date`, `Code`, `CoName`, `CoNameEn`, `S17`, `S17Nm`, `S33`, `S33Nm`, `ScaleCat`, `Mkt`, `MktNm`, `Mrgn`, `MrgnNm`, `ProdCat`

市場区分コード（`Mkt`）：

| コード | 名称 | 対象 |
|---|---|---|
| 0111 / 0112 / 0113 | プライム / スタンダード / グロース | ○ |
| 0101 / 0102 / 0104 | 東証一部 / 東証二部 / マザーズ（2022-04-03 まで） | ○ |
| 0106 / 0107 | JASDAQ スタンダード / グロース（2022-04-03 まで） | ○ |
| 0105 | TOKYO PRO MARKET | × |
| 0109 | その他 | × |

TODO：ETF・REIT・優先株を除外するための `ProdCat` などの値は、接続確認のときに確かめてここに記録する。

### プランごとの制約

| プラン | 日足の取得期間 | レート制限（リクエスト/分） |
|---|---|---|
| Free | 12週間前〜約2年前（12週間遅れ） | 5 |
| Light | 最大5年 | 60 |
| Standard | 最大10年 | 120 |
| Premium | 最大20年 | 500 |

- **過去5年の検証には Light プラン以上が必要。** Free プランでも接続確認はできる
- 前場・後場のデータは Premium のみ（このプロジェクトでは使わない）

## Normalized OHLCV（共通フォーマット）

long 形式（1 行 = 1 銘柄 × 1 日）とする。

| column | 型 | 説明 |
|---|---|---|
| date | datetime64 (tz-naive, JST 営業日) | 取引日 |
| symbol | str | 銘柄コード。J-Quants の5桁コードをそのまま使う（例：`72030`） |
| open | float | 始値 |
| high | float | 高値 |
| low | float | 安値 |
| close | float | 終値 |
| volume | float | 出来高 |

- 価格が調整後か未調整かを Provider 単位で明示する（メタデータ `adjusted: bool`）
- 欠損・異常値は `validate_ohlcv()` でチェックする
- TODO：high_price_breakout は「シグナルは調整後、約定は未調整」の両方を使う。共通フォーマットの拡張方法（調整後の列を追加する、または調整係数の列を持つ）は、データ取得を実装するときに決める

## データソース比較表

| 観点 | J-Quants API V2（第一候補） | 候補B (TODO) |
|---|---|---|
| 取得可能期間 | 2008-05-07〜。取れる範囲はプランで決まる（Light：5年） | |
| OHLCV の有無 | あり（売買代金 `Va` もある） | |
| 調整後株価 | あり（`Adj*`、`AdjFactor`） | |
| 上場廃止銘柄の有無 | あり（上場していた期間。接続確認で確かめる） | |
| API 制限（レート・件数） | 5〜500 リクエスト/分（プラン別）、ページングあり | |
| 利用条件・ライセンス・費用 | 有料プランあり。個人利用。再配布は不可の想定（規約を確認する） | |
| データ品質（欠損・誤り・更新遅延） | 取引所（JPX）が提供。無約定の日は null。Free は12週間遅れ | |
| 取得方法（API / CSV / スクレイピング） | REST API（Light 以上は CSV も可） | |
| 備考 | 公式の Python クライアント（jquants-api-client）がある | |

### 観点の説明

- **取得可能期間**：2 年・5 年・それ以上の検証に足りるか
- **OHLCV**：始値・高値・安値・終値・出来高がすべて揃うか
- **調整後株価**：分割・併合の調整有無、調整方法
- **上場廃止銘柄**：Survivorship bias 回避に必要
- **API 制限**：取得速度・1 日あたりの上限
- **利用条件**：商用利用可否、再配布可否（リポジトリにデータを含めない前提）
- **データ品質**：欠損・誤データの頻度、修正履歴

## 参考

- [J-Quants API Reference：V1 から V2 への変更点](https://jpx-jquants.com/ja/spec/migration-v1-v2)
- [J-Quants API Reference：日足](https://jpx-jquants.com/ja/spec/eq-bars-daily)
- [J-Quants API Reference：上場銘柄マスタ](https://jpx-jquants.com/ja/spec/eq-master)
- [J-Quants API Reference：プランごとの取得期間](https://jpx-jquants.com/ja/spec/data-spec)
