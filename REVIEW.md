# REVIEW

## 2026-04-26 導入レビュー

### P1: README にクローン前提の導入手順がない

- `README.md` には実行コマンドが載っているが、利用開始前にリポジトリを `git clone` するか ZIP 展開する必要があることが明記されていない。
- 非エンジニア視点では、`Set-Location "<AI-Nikki-root>"` の時点で「そのフォルダをどう用意するのか」が分からず、最初の一歩で止まりやすい。
- 最低でも以下の流れを README 冒頭に置いた方がよい。
  - リポジトリを取得する
  - Python 3.11 以上を用意する
  - 仮想環境を作る
  - `pip install -e .` でローカルインストールする

### P1: クリーン環境で CLI が起動せず、実使用前に停止する

- 検証コマンド:
  - `.\.venv\Scripts\python -m ai_nikki --help`
  - `.\.venv\Scripts\python -m unittest`
- 結果:
  - どちらも `ImportError: cannot import name 'ai_name_variants' from 'ai_nikki.personas'` で失敗。
- 観測内容:
  - `C:\Users\sgmxk\Desktop\AI\repos\github\harunamitrader\AI-Nikki\ai_nikki\reports.py` で `ai_name_variants` と `canonical_ai_name` を import している。
  - しかし `C:\Users\sgmxk\Desktop\AI\repos\github\harunamitrader\AI-Nikki\ai_nikki\personas.py` には該当シンボルが見当たらない。
- 影響:
  - README に書かれている `ingest` `prepare-personas` `generate-diaries` `sync` の前に、ヘルプ表示すらできず停止する。
  - 非エンジニア利用以前に、開発者でも初回確認で詰まる状態。

### P2: README にローカルインストール手順がない

- `pyproject.toml` はあるが、README には `pip install -e .` も通常インストールも書かれていない。
- 現状の README だけ読むと、`python -m ai_nikki ...` がそのまま実行できるように見える。
- 実際には少なくともパッケージ配置か実行位置の理解が必要なので、非エンジニア向けには導入手順を明文化した方がよい。

### P2: スキル実行の依頼文例がなく、非エンジニアには始め方が分かりにくい

- README の「初回の流れ」で `ai-nikki-historical-ingest` スキルを使う案内はあるが、ユーザーが AI にどう頼めばよいかの具体例がない。
- 非エンジニア視点では「スキル名は分かったが、どの文を送れば始まるのか」で止まりやすい。
- 少なくとも以下のような送信例を README に置いた方がよい。
  - `AI-Nikki の初回セットアップを始めたいです。ai-nikki-historical-ingest の流れで、使えるログ候補を探して確認しながら進めてください。`
  - `AI-Nikki の persona 設定を作りたいです。ai-nikki-persona-setup の流れで進めてください。`

### P2: 初回インポートの所要量・所要時間の目安がなく、実行前に身構えにくい

- 実データで全候補を取り込むと、初回 `ingest` は 1150 ファイル処理、`ai_logs.sqlite` は約 1.3 GB になった。
- 非エンジニア視点では「押したらどれくらい待つのか」「ディスクをどれくらい使うのか」が見えないと不安が大きい。
- README か初回フロー案内に、少なくとも「ログ量によっては数十秒以上かかる」「DB が大きくなることがある」旨の注意書きがあると親切。

### P2: 生成日記にシステム由来の断片が混ざることがあり、そのままだと読みにくい

- 実データの `2026-04-26-ai-nikki.md` では、`<environment_cont…` や `<current_datetime…` のようなシステム文脈由来の断片が本文に出ている。
- これはログとしては正しくても、日記として読むとノイズが強い。
- 非エンジニア向けには「どこまでが日記らしい本文になるか」に直結するので、プロンプト断片や system/context 系文字列の除外ルールを追加した方がよい。

### 対応メモ: LLM執筆ワークフローへ変更

- 日記本文をプログラムの固定テンプレートで生成すると、AIの感情や読み物としての面白さが出にくい。
- そのため、AI-Nikki 本体は素材生成、writer prompt 作成、draft 検査、公開処理を担当し、本文は Codex や Claude Code などの AI ツールがスキル実行時に書く方針へ変更した。
- 検査では、`...` / `…` による雑な途中省略、UUID、Windows パス、メタタグ、140文字超過、Markdown と JSON の不一致を検出する。
- 3回検査に失敗した場合も draft は捨てず、`YYYY-MM-DD-ai-nikki-review-needed.md` に失敗理由を残す。
