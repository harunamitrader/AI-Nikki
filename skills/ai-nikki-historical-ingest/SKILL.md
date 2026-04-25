---
name: ai-nikki-historical-ingest
description: 既存の AI ログを見つけて一元化 DB を作る初回用スキル。1つのツールだけでも実行可能。
---

# AI-Nikki Historical Ingest

初回の**過去ログ一元化**だけを行うスキルです。

## ゴール

1. ユーザー環境にある AI ログ候補を見つける
2. どの取得元を使うか確認する
3. 選ばれた取得元だけを設定に反映する
4. 一元化 DB を作る

**日記生成や性格設定には進まないこと。**

## 実行手順

1. `<AI-Nikki-root>` を確認する
2. `config\ai-nikki.json` を読む
3. 実際のログ候補を調べる
4. ユーザーに **必ず** 確認する  
   **「ログファイルを取得するAIツールはこれでいいですか？除外したいものがあれば教えてください。」**
5. ユーザーが選んだ取得元だけを `config\ai-nikki.local.json` に書く
6. 実行する

```powershell
Set-Location "<AI-Nikki-root>"
python -m ai_nikki ingest
```

7. 次だけ確認する

- `data\unified\ai_logs.sqlite`

## Guardrails

- 1 source だけでも正常フローとして扱う
- ログが見つからなかった source を「取得した」と言わない
- ユーザー確認前に取得元を確定しない
- secrets や token を露出しない
