# Claude Code のクラウド環境で動かす

作成：2026-10-02。実行環境を、Windows の PC から Claude Code のクラウド環境（cloud session）に移すための手順。
方針：**株データは git に入れない**（J-Quants の規約でデータそのものの共有が禁止されているため）。クラウドでは J-Quants API から取得する。

## 1. 重要：古いデータはもう API から取れない

Light プランで取れるのは「今日から約5年前まで」で、**取得できる最初の日は毎日1日ずつ進む**（2026-09-29 の確認時点で 2021-09-29 から）。

| 区間 | 必要なデータ | API から取れるか |
|---|---|---|
| 開発用（2021-10-27〜2024-04-02） | 2021-09-29 から（直前20営業日を指標に使う） | **取れない。2026-10 の時点で、すでに最初の数日分が取得範囲の外**。2026-10-27 ごろには開発用期間の初日も外れる |
| 最終評価用・閲覧済み・追加確認用 | 2024-03 以降 | 当面は取れる（2029年ごろまで） |

- 開発用期間を再実行する（新しい戦略を同じ条件で比べる、など）には、**PC に保存してある取得済みデータが唯一の元データ**になる
- **PC のデータを、すぐにバックアップする**（2章）
- データの最初の日が足りない場合、`run_backtest.py` は「ウォームアップが足りない」として実行を拒否する（黙って短い期間で計算はしない）

## 2. PC のデータのバックアップ（最優先）

PC（PowerShell）で：

```powershell
git pull
python scripts/pack_data.py
```

- `data/archive/` に `jquants_raw_<日付>.zip` と `jquants_processed_<日付>.zip` を作る（git 管理外）
- `docs/data_archives/<日付>.json` に、大きさ・SHA-256・ファイル数・加工済みデータの manifest を書く。**この JSON だけを commit する**（データそのものは含まない）
- zip は**自分専用の保存場所**（Google Drive など）に置く。**共有リンクは作らない**。他のサービス（ChatGPT など）からそのフォルダを読ませない

戻すとき（どの環境でも）：

```bash
python scripts/restore_data.py docs/data_archives/<日付>.json data/archive/jquants_raw_<日付>.zip data/archive/jquants_processed_<日付>.zip
```

SHA-256 とファイル数が記録と一致した場合だけ展開する。既存のファイルは `--overwrite` を付けない限り上書きしない。

## 3. クラウド環境の設定（claude.ai/code の環境設定）

| 項目 | 設定 |
|---|---|
| ネットワーク | **Custom**。Allowed domains に `api.jquants.com` を書き、「Also include default list of common package managers」に印を付ける（PyPI など） |
| API credentials（Pro / Max プランのみ） | Host：`api.jquants.com`。Custom headers の Name：`x-api-key`、Prefix：空、Value：J-Quants の API キー。**キーは Claude からも実行するコマンドからも見えない** |
| 環境変数 | `JQUANTS_API_KEY_VIA_PROXY=1`（キーは通信の途中で付けてもらう、という意味。キー自体は書かない） |
| セットアップスクリプト | `pip install --break-system-packages -e ".[dev]"`（約5分以内に終わればキャッシュされ、次のセッションから省略される） |

- **API キーを環境変数に書かない**（環境変数は、その環境を使う人と Claude から見える）
- Team / Enterprise プランでは API credentials がまだ使えない。その場合のキーの扱いは別に決める

## 4. クラウドでの作業の流れ

```bash
# 自動テスト（株データ不要。数十秒）
pytest

# 実データが必要なとき：取得 → 加工 → 確認（同じセッション中は再取得不要）
python scripts/download_data.py --start <開始日> --end <終了日> --min-interval 1.1
python scripts/process_data.py --start <開始日> --end <終了日>
python scripts/check_periods.py
python scripts/check_data_quality.py
```

- 取得したデータは、そのセッションのマシンが片付けられるまで残る（しばらく使わず一時停止しても残る）
- 取得は途中で止まっても、同じコマンドで続きから再開できる
- 取得範囲の開始日は、1章のとおり API の取得範囲に収まる日にする。開発用期間を使う場合は、2章のバックアップを戻す

## 5. 未決定の事項

| # | 内容 |
|---|---|
| 1 | バックアップの zip を、クラウドのセッションに入れる方法（Google Drive からの取り込みには認証情報が必要。キーを見せない方法が使えるかを含めて検討する） |
| 2 | Team / Enterprise プランの場合の API キーの扱い |
