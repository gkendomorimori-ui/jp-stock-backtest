# CLAUDE.md

Claude Code がこのリポジトリで作業する際に参照する開発ルール。

## Your Role

Claude Code は主に **実装担当**。

ChatGPT 側で作成された戦略仕様・検証条件を読み、再現可能な Python コードとして実装する。
担当範囲：Python 実装、テスト、リファクタリング、ファイル操作、バックテスト実行、Git 操作、バグ修正。

投資アイデアの発案・戦略の良し悪しの判断は主担当ではない。気付いた点は「提案」として報告し、勝手に仕様へ反映しない。

## Important Rules

1. 未来情報を使用しない
2. Look-ahead bias を防止する
   - Day T のシグナルは Day T 時点までに確定したデータのみで計算する
   - 約定は原則 Day T+1（詳細は docs/BACKTEST_RULES.md）
   - `shift` の向き、ローリング計算の窓、データ結合時の日付ずれに注意する
3. Survivorship bias を意識する
   - 「現在上場している銘柄」だけで過去を検証していないか確認し、制約があれば結果に明記する
4. 売買手数料を考慮する
5. スリッページを設定可能にする
6. データ取得と戦略ロジックを分離する
   - 戦略コードから特定のデータソースを直接呼ばない。`src/data` の `DataProvider` 経由で正規化済み OHLCV を受け取る
7. 戦略パラメータをハードコードしない
   - パラメータは設定ファイル／引数から注入する。コード中のデフォルト値を使う場合は仕様書の値と一致させる
8. 同じ条件なら同じ結果を再現できるようにする
   - 乱数を使う場合は seed を設定値として保存する
   - 実行時の設定・Git commit hash を結果と一緒に保存する
9. バックテスト結果を CSV/JSON 等の機械可読形式で保存する
10. 変更時には可能な限りテストを追加する

## Before Implementation

実装前に必ず、

- PROJECT_CONTEXT.md
- docs/BACKTEST_RULES.md
- 対象戦略の仕様書（strategies/<strategy_name>.md）

を確認する。

仕様が曖昧な場合、独自判断で投資ルールを追加しない。
曖昧な点は質問として列挙し、暫定実装が必要な場合は `TODO(spec):` コメントで明示する。

## Coding Conventions

- Python 3.11+
- 型ヒントと docstring を付ける（公開関数・クラスは必須）
- フォーマッタ／リンタ：ruff（`ruff format .` / `ruff check .`）
- 型チェック：mypy（`mypy src`）
- テスト：pytest（`pytest`）
- パッケージは `src` をルートとして `from src.data import ...` の形でインポートする
- 設定値の仮置きは `TODO` と明記し、確定値と区別する

## Git

- コミットメッセージは Conventional Commits 形式（`feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`）
- `data/raw/`, `data/processed/`, `results/runs/` はコミットしない
- 明示的な指示なしに remote 設定・push を行わない

## Done の定義

- テストが通る（`pytest`）
- lint が通る（`ruff check .`）
- 新しい戦略・指標にはテストがある
- バックテスト実行時は summary.json / trades.csv / equity_curve.csv が出力される
