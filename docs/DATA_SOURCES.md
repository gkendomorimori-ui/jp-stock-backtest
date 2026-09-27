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
| 取引カレンダー | `GET /v2/markets/calendar`（`HolDiv`：0 非営業日 / 1 営業日 / 2 東証半日立会 / 3 非営業日（祝日取引あり））。東証の営業日は 1 と 2 |
| TOPIX | `GET /v2/indices/bars/daily/topix`（`O` `H` `L` `C`。データがない日はレコード自体が返らない） |
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

商品区分コード（`ProdCat`、公式仕様で確認 2026-09-27）：

| コード | 名称 | 対象 |
|---|---|---|
| 011 | 国内株式 | ○（ただし下記の注意） |
| 012 | 優先出資証券 | × |
| 013 | REIT | × |
| 014 | ETF | × |
| 021 | 外国株式 | × |
| 022 | 外国REIT | × |
| 023 | 外国ETF | × |
| 024 | 外国預託証券 | × |

- 公式仕様によると、`011`（国内株式）には**普通株と優先株の両方**が含まれる。`ProdCat` だけでは普通株を取り出せない
- 方針：普通株だと確定できないものは、**推測で普通株に含めない**（`unclassified_policy: exclude`）
- `scripts/check_jquants_connection.py` は、仮の判定として「`ProdCat = 011` かつ5桁コードの末尾が `0`」を普通株の**候補**とし、それ以外の `011` と未知のコードを**分類保留**として件数と銘柄を表示する。この仮の判定が正しいかは、実データと照合してから確定する
- TODO：実データと照合した結果（分類保留の件数と中身、最終的な判定ルール）をここに記録する

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
| 上場廃止銘柄の有無 | あり（上場していた期間。既知の上場廃止銘柄で接続確認する） | |
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

## 接続確認（未実施）

`scripts/check_jquants_connection.py` で、少量のリクエストで以下を確認する（手順は README）。

| 確認項目 | 状態 |
|---|---|
| API キーで日足・銘柄マスタ・カレンダー・TOPIX が取得できる | 未確認 |
| `ProdCat` と市場区分の実データでの件数、分類保留の件数 | 未確認 |
| 既知の上場廃止銘柄の、上場期間中の銘柄情報と株価が取得できる | 未確認 |

- 現在の銘柄一覧に存在しないことだけで「取得不可」とは判断しない。契約の取得可能期間内に上場廃止となった既知の銘柄を使って確かめる
- Free プランでの確認は**動作確認**であり、5年分の正式な評価とは区別する

## 参考

- [J-Quants API Reference：V1 から V2 への変更点](https://jpx-jquants.com/ja/spec/migration-v1-v2)
- [J-Quants API Reference：日足](https://jpx-jquants.com/ja/spec/eq-bars-daily)
- [J-Quants API Reference：上場銘柄マスタ](https://jpx-jquants.com/ja/spec/eq-master)
- [J-Quants API Reference：プランごとの取得期間](https://jpx-jquants.com/ja/spec/data-spec)
- [J-Quants API Reference：市場区分コード](https://jpx-jquants.com/ja/spec/eq-master/marketcode)
- [J-Quants API Reference：商品区分コード](https://jpx-jquants.com/ja/spec/eq-master/product-category)
- [J-Quants API Reference：取引カレンダー](https://jpx-jquants.com/ja/spec/mkt-cal)
- [J-Quants API Reference：TOPIX](https://jpx-jquants.com/ja/spec/idx-bars-daily-topix)
