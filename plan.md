# AI-Nikki 計画メモ

## 現在の目的

- 複数の AI ツールのログを 1 つにまとめる
- 03:00 区切りで日次データを作る
- AI ごとの視点で X 投稿向け日記を 1 日 1 ファイルで出力する
- 将来の自動投稿に備えて投稿単位 JSON も出力する

## 現在の実装方針

- プロジェクト名: `AI-Nikki`
- リポジトリ: `C:\Users\sgmxk\Desktop\AI\repos\github\harunamitrader\AI-Nikki`
- Python モジュール: `ai_nikki`
- 正本 DB: `data\unified\ai_logs.sqlite`
- 日次日記: `reports\daily\YYYY-MM-DD-ai-nikki.md`
- 投稿JSON: `reports\daily\YYYY-MM-DD-ai-nikki-posts.json`
- 性格設定: `config\ai-nikki-personas.local.md`

## ユーザーフロー

1. `ai-nikki-historical-ingest`
2. `ai-nikki-persona-setup`
3. `ai-nikki-historical-diary-generation`
4. `ai-nikki-daily-ingest`
5. `ai-nikki-daily-missing-diaries`

## メモ

- 1つのログ取得元だけでも正常フローとして扱う
- `historical-ingest` 後に取得元確認を入れる
- 不要な取得元は `config\ai-nikki.local.json` で除外できる
- 性格設定は Markdown / 日本語項目
- 固定の締め文は持たない
- 不活動時の文も固定設定せず、生成時に性格に応じて出す
