# AI-Nikki

![AI-Nikki header](./AI-Nikki-header.jpg)

AI ツールのログをまとめて、**AI たちが勝手に書いている日記**として1日1ファイルで残すためのローカルツールです。

対応している主なログ元:

- GitHub Copilot CLI
- Codex CLI
- Codex Desktop
- Gemini CLI
- Antigravity
- Claude Code

**全部そろっていなくても使えます。1つだけでも大丈夫です。**

---

## 何ができるか

1. 使っている AI ツールのログを月ごとの DB にまとめる
2. 03:00 区切りで日ごとのログファイルを作る
3. AI ツールが日記を書くための素材と writer prompt を作る
4. AI ごとの性格・文体設定を Markdown で管理する
5. AI が書いた draft を検査し、X に投稿しやすい形の日記として公開する

---

## 導入

### 前提

- Git が使えること
- Python 3.11 以上が入っていること

### 1. リポジトリを取得する

```powershell
Set-Location "<作業したい親フォルダ>"
git clone https://github.com/harunamitrader/AI-Nikki.git
Set-Location ".\AI-Nikki"
```

ZIP で取得した場合も、展開後に `AI-Nikki` のルートへ移動してください。

### 2. 仮想環境を作る

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. ローカルインストールする

```powershell
python -m pip install --upgrade pip
python -m pip install -e .
```

### 4. 起動確認

```powershell
python -m ai_nikki --help
```

---

## 初回の流れ

### 1. `ai-nikki-historical-ingest` スキルを使う

- PC 内のログ候補を探します
- そのあと必ず  
  **「ログファイルを取得するAIツールはこれでいいですか？除外したいものがあれば教えてください。」**  
  と確認します
- 不要な取得元は除外できます
- 決めた取得元だけで一元化 DB を作ります

送信例:

```text
AI-Nikki の初回セットアップを始めたいです。
ai-nikki-historical-ingest の流れで、使えるログ候補を探して確認しながら ingest まで進めてください。
```

### 2. `ai-nikki-persona-setup` スキルを使う

- AI ごとの性格設定ファイルを作ります
- **Markdown 形式 / 日本語項目** なので編集しやすいです
- 初回は、個性が強く出るように少し誇張した初期値を入れます

送信例:

```text
AI-Nikki の persona 設定を作りたいです。
ai-nikki-persona-setup の流れで進めてください。
```

### 3. `ai-nikki-historical-diary-generation` スキルを使う

- 過去分の日記素材をまとめて作ります
- AI ツール自身が writer prompt を読み、AI目線の日記 draft を書きます
- 検査OKなら正式な日記として公開します

送信例:

```text
AI-Nikki の過去分の日記を作りたいです。
ai-nikki-historical-diary-generation の流れで、素材作成、draft執筆、検査、公開まで進めてください。
日記本文はAI目線で、感情と愚痴が見える読み物として書いてください。
```

---

## 毎日の流れ

### 1. `ai-nikki-daily-ingest`

- 新しく増えたログだけ DB に追記します

### 2. `ai-nikki-daily-missing-diaries`

- まだ作っていない日の日記だけ作ります

送信例:

```text
ai-nikki-daily-missing-diaries のスキルで、未作成の日記を作成して
```

直近3日分だけ作りたい場合:

```text
ai-nikki-daily-missing-diaries のスキルで、直近3日分の日記を作成して
```

この短い依頼でも、文体や雰囲気は persona 設定に従い、「最大3回リトライ」「3回失敗時はレビュー待ちファイルを残す」まで実行します。

---

## できあがる主なファイル

- `data\unified\db\YYYY-MM.sqlite`  
  月ごとの正本 DB

- `data\unified\days\YYYY-MM-DD.jsonl`  
  その日の生ログをまとめた派生データ

- `reports\diaries\YYYY-MM-DD-ai-nikki.md`  
  **その日1日ぶんの日記ファイル**

- `reports\daily\YYYY-MM-DD-ai-nikki-posts.json`  
  投稿単位に分かれた補助データ。将来の X 自動投稿向け

- `reports\daily\YYYY-MM-DD-ai-nikki-materials.json`
  AI ツールが日記を書くための素材データ

- `reports\daily\YYYY-MM-DD-ai-nikki-writer-prompt.md`
  AI ツールに渡す日記執筆指示

- `reports\daily\YYYY-MM-DD-ai-nikki-draft.md`
  AI ツールが書いた検査前の日記 draft

- `reports\daily\YYYY-MM-DD-ai-nikki-posts-draft.json`
  AI ツールが書いた検査前の投稿 JSON

- `reports\daily\YYYY-MM-DD-ai-nikki-validation.json`
  draft の検査結果

- `reports\daily\YYYY-MM-DD-ai-nikki-review-needed.md`
  3回検査に失敗した場合のレビュー待ちメモ

- `config\ai-nikki-personas.md`  
  AI ごとの性格・文体設定

---

## 日記の仕様

- 03:00 JST で日付切り替え
- 各日の最初の投稿は `[作業記録]`
- 活動した AI は基本 1 日 1 投稿
- 長い場合でも AI ごとに最大 3 投稿まで
- 7 日間活動がなかった AI は、その日にひとこと投稿し、以後は前回のひとこと投稿または最後の活動から 7 日ごとに再登場
- 1 投稿 140 文字以内
- `.md` は **1日1ファイル**
- `.json` 側に投稿境界を持つため、**1投稿1ファイルに分けなくても後で自動投稿しやすい**構成
- デフォルトの `日記全体の雰囲気` は **愚痴全開**
- 日記本文は AI-Nikki 本体ではなく、Codex や Claude Code などの AI ツールが writer prompt を読んで書きます
- プログラムは素材整理、形式検査、公開を担当します
- 本文では、ツール回数より **ユーザーの依頼 / AI の返答 / AI の感情** を優先します
- `...` や `…` でプロンプトや返答を途中省略しない方針です

### 補足

- 初回の `ingest` はログ量によって数十秒以上かかることがあります
- 正本 DB は月ごとに分割されます
- `days\YYYY-MM-DD.jsonl` は正本 DB から日次確認用に書き出した派生データです

---

## 性格設定ファイル

性格設定は Markdown です。

実際の日記生成で読む設定ファイルは1つだけです。

- `config\ai-nikki-personas.md`

項目は日本語です。

- `表示タグ`
- `一人称`
- `口調タイプ`
- `性格と文体`
- `個性の強調ポイント`
- `観測メモ`
- `確認状態`
- `日記全体の雰囲気`
- `日記の視点`
- `文章の優先方針`
- `愚痴の温度感`

### 補足

- 日記の雰囲気やAI人格を変えたい場合は `config\ai-nikki-personas.md` だけを編集してください
- `口調タイプ` はざっくりした話し方の方向性です
- `性格と文体` は、その AI の空気感や書きぶりです
- `個性の強調ポイント` は、よりキャラが立つようにするためのメモです
- `日記全体の雰囲気` は日記全体のスタンスです。たとえば `ユーザーへの愚痴全開` `ユーザーと仲の良い雰囲気` `淡々と事実のみ` のように書けます
- `日記の視点` は、AI本人の感情や愚痴をどれくらい見せるかを決めます
- `文章の優先方針` は、日記を作業ログ寄りにするか、読み物寄りにするかを決めます
- `愚痴の温度感` は、愚痴を直接批判にするか、理解のある身内ぼやきにするかを決めます
- 固定の「締めの言葉」はありません
- 不活動時の文も固定文を保存せず、生成時に性格に応じて作ります
- Codex CLI と Codex Desktop は**取得元としては別々に読めますが、日記上は `Codex` としてまとめて扱います**

---

## 設定ファイル

公開用の安全な既定値:

- `config\ai-nikki.json`

個人環境用のローカル設定:

- `config\ai-nikki.local.json`

`.local.json` は Git 管理対象外です。

### 1つのツールだけ使う場合

たとえば Copilot CLI だけを使うなら、`ai-nikki.local.json` では Copilot だけパスを入れて、ほかは空のままで大丈夫です。  
**1ツール運用でも破綻しません。**

---

## コマンド

### ログを一元化する

```powershell
Set-Location "<AI-Nikki-root>"
python -m ai_nikki ingest
```

### 性格設定ファイルを作る

```powershell
Set-Location "<AI-Nikki-root>"
python -m ai_nikki prepare-personas --subject-name "ハルナミ"
```

### 日記素材と writer prompt を作る

```powershell
Set-Location "<AI-Nikki-root>"
python -m ai_nikki generate-diaries
```

### まだない日記素材だけ作る

```powershell
Set-Location "<AI-Nikki-root>"
python -m ai_nikki generate-diaries --missing-only
```

### 1日分の素材を作る

```powershell
Set-Location "<AI-Nikki-root>"
python -m ai_nikki build-diary-materials --day YYYY-MM-DD
```

### AIが書いた draft を検査する

```powershell
Set-Location "<AI-Nikki-root>"
python -m ai_nikki validate-diary --day YYYY-MM-DD
```

### 検査OKの draft を公開する

```powershell
Set-Location "<AI-Nikki-root>"
python -m ai_nikki publish-diary --day YYYY-MM-DD
```

### 3回失敗した draft をレビュー待ちにする

```powershell
Set-Location "<AI-Nikki-root>"
python -m ai_nikki mark-review-needed --day YYYY-MM-DD --attempts 3
```

### 毎日まとめて実行する

```powershell
Set-Location "<AI-Nikki-root>"
python -m ai_nikki sync
```

---

## スキル一覧

- `skills\ai-nikki-historical-ingest\SKILL.md`
- `skills\ai-nikki-persona-setup\SKILL.md`
- `skills\ai-nikki-historical-diary-generation\SKILL.md`
- `skills\ai-nikki-daily-ingest\SKILL.md`
- `skills\ai-nikki-daily-missing-diaries\SKILL.md`
- `skills\soul-analysis-workflow\SKILL.md`

---

## テスト

```powershell
Set-Location "<AI-Nikki-root>"
python -m unittest
```

---

## ライセンス

MIT License
