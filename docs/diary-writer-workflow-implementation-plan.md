# 日記品質改善 実装計画

作成日: 2026-04-26

## ゴール

AI-Nikki 本体は日記本文を機械的に生成しない。AI CLI / Claude Code / Codex などの AI ツールがスキルとして日記本文を書く。AI-Nikki 本体は素材整理、執筆指示、形式検査、公開処理を担当する。

## 手順と検証

1. 素材生成コマンドを追加する
   - `build-diary-materials --day YYYY-MM-DD`
   - 検証: `data/unified/days/YYYY-MM-DD.jsonl`、`reports/daily/YYYY-MM-DD-ai-nikki-materials.json`、`reports/daily/YYYY-MM-DD-ai-nikki-writer-prompt.md` が作られる。

2. 日記本文の検査コマンドを追加する
   - `validate-diary --day YYYY-MM-DD`
   - 検証: draft Markdown / draft JSON を検査し、`YYYY-MM-DD-ai-nikki-validation.json` に OK / NG と理由を残す。

3. 公開コマンドを追加する
   - `publish-diary --day YYYY-MM-DD`
   - `publish-diary --day YYYY-MM-DD --force`
   - 検証: OK の draft は正式な `YYYY-MM-DD-ai-nikki.md` と `YYYY-MM-DD-ai-nikki-posts.json` にコピーされる。NG は通常公開しないが、`--force` なら公開できる。

4. レビュー待ちコマンドを追加する
   - `mark-review-needed --day YYYY-MM-DD --attempts 3`
   - 検証: 3回失敗後も draft を捨てず、`YYYY-MM-DD-ai-nikki-review-needed.md` に失敗理由と手動修正案を残す。

5. スキル手順を更新する
   - AIツールは writer prompt を読んで draft を書く。
   - 検査NGなら最大3回までAIが書き直す。
   - 3回失敗した場合も draft / validation / review-needed を残す。

6. 既存コマンドの意味を安全に変更する
   - `generate-diaries` は正式日記を機械生成せず、未作成日の素材と writer prompt を作る互換コマンドにする。
   - `sync` も ingest 後に素材と writer prompt を作るだけにする。

