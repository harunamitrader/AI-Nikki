---
name: ai-nikki-historical-diary-generation
description: 一元化済みログから過去分の AI-Nikki 日記を、AIツール自身が本文を書いて作るスキル。1つの AI だけでも実行可能。
---

# AI-Nikki Historical Diary Generation

一元化済み DB から、**過去分の日記をまとめて作る**スキルです。

AI-Nikki 本体は素材と writer prompt を作ります。日記本文は、persona 設定に従ってあなたが書きます。

## ゴール

1. 日次 JSONL を作る
2. 日記執筆用の素材 JSON を作る
3. AIツール向け writer prompt を作る
4. あなたが draft Markdown / draft JSON を書く
5. 検査OKなら正式な `AI-Nikki` 日記として公開する

## 実行

```powershell
Set-Location "<AI-Nikki-root>"
python -m ai_nikki generate-diaries
```

この時点で作られるのは素材と writer prompt です。

- `data\unified\days\YYYY-MM-DD.jsonl`
- `reports\daily\YYYY-MM-DD-ai-nikki-materials.json`
- `reports\daily\YYYY-MM-DD-ai-nikki-writer-prompt.md`

各日ごとに writer prompt と materials JSON を Read し、次の draft をあなたが Write します。

- `reports\daily\YYYY-MM-DD-ai-nikki-draft.md`
- `reports\daily\YYYY-MM-DD-ai-nikki-posts-draft.json`

検査します。

```powershell
python -m ai_nikki validate-diary --day YYYY-MM-DD
```

NG の場合は、`reports\daily\YYYY-MM-DD-ai-nikki-validation.json` を Read して draft を修正します。最大3回まで繰り返します。

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

## Guardrails

- DB が無いなら日記を捏造しない
- 1投稿1ファイルにはしない
- 1 source だけでもそのまま書く
- `...` や `…` でプロンプトを途中省略しない
- 日記の文体、視点、雰囲気は `config\ai-nikki-personas.md` を優先する
