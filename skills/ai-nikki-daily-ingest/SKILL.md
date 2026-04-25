---
name: ai-nikki-daily-ingest
description: 新しく増えたログだけを一元化 DB に追記する日次用スキル。1 source でも動作する。
---

# AI-Nikki Daily Ingest

**日次の差分取り込み**専用スキルです。

## 実行

```powershell
Set-Location "<AI-Nikki-root>"
python -m ai_nikki ingest
```

## Guardrails

- 1つの取得元しか設定されていなくても正常
- `config\ai-nikki.local.json` に入っている source だけが対象
- diary 生成まではしない
