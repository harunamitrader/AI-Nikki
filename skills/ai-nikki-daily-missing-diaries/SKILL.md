---
name: ai-nikki-daily-missing-diaries
description: まだ作っていない日の日記だけ作る日次用スキル。1 source でも動作する。
---

# AI-Nikki Daily Missing Diaries

**未作成日記だけを埋める**専用スキルです。

## 実行

```powershell
Set-Location "<AI-Nikki-root>"
python -m ai_nikki generate-diaries --missing-only
```

## 確認ファイル

- `reports\daily\YYYY-MM-DD-ai-nikki.md`
- `reports\daily\YYYY-MM-DD-ai-nikki-posts.json`

## Guardrails

- 1つの AI だけでも正常
- 再取り込みはしない
- 既存日を全部作り直さない
