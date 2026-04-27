---
name: ai-nikki-daily-missing-diaries
description: まだ作っていない日の日記だけ、AIツール自身が本文を書いて作る日次用スキル。1 source でも動作する。
---

# AI-Nikki Daily Missing Diaries

**未作成日記だけを埋める**専用スキルです。

このスキルでは、AI-Nikki 本体は日記本文を書きません。あなたが素材と writer prompt を読み、AI目線の日記を draft として書き、検査に通ったら公開します。

## 実行方針

- 日記本文は LLM であるあなたが書く
- プログラムは素材整理、形式検査、公開だけを担当する
- 日記の文体、視点、雰囲気は `config\ai-nikki-personas.md` を優先する
- ユーザーの依頼が短くても、persona 設定、最大3回リトライ、失敗時レビュー待ちを既定動作にする
- `...` や `…` でプロンプトを途中省略しない
- 検査NGでも draft は捨てない

## ユーザーの依頼例

ユーザーは短く頼んでよいです。

```text
ai-nikki-daily-missing-diaries のスキルで、直近3日分の日記を作成して
```

日付を明示したい場合も、次の程度で十分です。

```text
ai-nikki-daily-missing-diaries のスキルで、直近3日分（2026-04-24、2026-04-25、2026-04-26）の日記を作成して
```

上の短い依頼でも、このスキルは次を必ず実行します。

- persona 設定の視点、雰囲気、文章方針に従って書く
- 検査に失敗したら最大3回まで書き直す
- 3回失敗したら draft を残してレビュー待ちファイルを作る

## 手順

```powershell
Set-Location "<AI-Nikki-root>"
python -m ai_nikki ingest
python -m ai_nikki generate-diaries --missing-only
```

`generate-diaries --missing-only` は、未作成日の素材と writer prompt を作る互換コマンドです。正式な日記本文はまだ作られません。

対象日ごとに、次のファイルを Read します。

- `reports\daily\YYYY-MM-DD-ai-nikki-writer-prompt.md`
- `reports\daily\YYYY-MM-DD-ai-nikki-materials.json`

writer prompt と materials の指示に従い、次の draft 2ファイルをあなたが Write します。

- `reports\daily\YYYY-MM-DD-ai-nikki-draft.md`
- `reports\daily\YYYY-MM-DD-ai-nikki-posts-draft.json`

その後、検査します。

```powershell
python -m ai_nikki validate-diary --day YYYY-MM-DD
```

NG の場合は、`reports\daily\YYYY-MM-DD-ai-nikki-validation.json` を Read し、最大3回まで draft を書き直します。

OK なら公開します。

```powershell
python -m ai_nikki publish-diary --day YYYY-MM-DD
```

3回目の検査でもNGだった場合は、draft を残してレビュー待ちにします。

```powershell
python -m ai_nikki mark-review-needed --day YYYY-MM-DD --attempts 3
```

## 確認ファイル

- `reports\diaries\YYYY-MM-DD-ai-nikki.md`
- `reports\daily\YYYY-MM-DD-ai-nikki-posts.json`
- `reports\daily\YYYY-MM-DD-ai-nikki-validation.json`
- `reports\daily\YYYY-MM-DD-ai-nikki-review-needed.md`

## Guardrails

- 1つの AI だけでも正常に書く
- 再取り込みはしてよいが、既存の公開済み日記を勝手に壊さない
- DB が無い日の日記を捏造しない
- 内部パス、UUID、セッションID、メタタグを日記本文に出さない
