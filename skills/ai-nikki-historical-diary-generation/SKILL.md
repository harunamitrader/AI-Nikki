---
name: ai-nikki-historical-diary-generation
description: 一元化済みログから過去分の AI-Nikki 日記を作るスキル。1つの AI だけでも実行可能。
---

# AI-Nikki Historical Diary Generation

一元化済み DB から、**過去分の日記をまとめて作る**スキルです。

## ゴール

1. 日次 JSONL を作る
2. 1日1ファイルの `AI-Nikki` 日記 `.md` を作る
3. 将来の X 自動投稿向けに投稿単位 JSON も作る

## 実行

```powershell
Set-Location "<AI-Nikki-root>"
python -m ai_nikki generate-diaries
```

## 確認ファイル

- `data\unified\days\YYYY-MM-DD.jsonl`
- `reports\daily\YYYY-MM-DD-ai-nikki.md`
- `reports\daily\YYYY-MM-DD-ai-nikki-posts.json`

## Guardrails

- DB が無いなら日記を捏造しない
- 1投稿1ファイルにはしない
- 1 source だけでもそのまま出力する
