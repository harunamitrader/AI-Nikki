---
name: ai-nikki-persona-setup
description: AI ごとの性格・文体設定 Markdown を作るスキル。historical-ingest のあとに使う。
---

# AI-Nikki Persona Setup

一元化 DB 作成後に、**AI ごとの性格設定 Markdown** を作るスキルです。

## ゴール

1. 一元化 DB を読む
2. AI ごとの日本語 Markdown 設定ファイルを作る
3. ユーザーが後で編集しやすい状態で止める

## 重要

- 1つの AI しか入っていなくても正常
- 固定の締め文は持たない
- 不活動時の文も固定保存しない
- 初回は個性が強く出るように少し誇張した案を入れる

## 実行

```powershell
Set-Location "<AI-Nikki-root>"
python -m ai_nikki prepare-personas --subject-name "ハルナミ"
```

## 確認ファイル

- `config\ai-nikki-personas.local.md`

## ユーザーへの伝え方

このファイルは:

- Markdown 形式
- 項目名は日本語
- AI ごとの一人称・口調・性格の強調を編集できる
- 後の diary generation に使われる
