from __future__ import annotations

from typing import Any


def _short_material_text(value: Any, limit: int = 90) -> str:
    raw = str(value or "")
    if "data:image" in raw:
        raw = raw.split("data:image", 1)[0].rstrip() + " [画像添付]"
    text = " ".join(raw.split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "[要約]"


def _is_useful_example(value: Any) -> bool:
    text = " ".join(str(value or "").split())
    if len(text) < 8:
        return False
    lowered = text.lower()
    if ":\\" in text:
        return False
    noise_prefixes = (
        "task_started",
        "token_count",
        "reasoning ",
        "none",
        "report_intent",
        "tool_use ",
        "thinking ",
        "bash ",
        "event_msg",
        "tool_request",
        "turn_context",
        "attachment",
        "hook_success",
        "hook_error",
        "session started",
        "task_reminder",
        "/remote-control",
        "- update-config",
        "1. ---",
    )
    if lowered in {"no response requested.", "none", "reasoning", "response_item", "skill_listing", "function_call"}:
        return False
    if lowered == "intent logged" or lowered.startswith("hook_"):
        return False
    if "claude.ai/code/session_" in lowered:
        return False
    if "from __future__ import annotations" in lowered:
        return False
    return not lowered.startswith(noise_prefixes)


def _first_useful_text(items: list[Any], *keys: str) -> str | None:
    for item in items:
        if isinstance(item, dict):
            for key in keys:
                if key in item and _is_useful_example(item.get(key)):
                    return str(item.get(key))
        elif _is_useful_example(item):
            return str(item)
    return None


def _useful_texts(items: list[Any], *keys: str, max_items: int) -> list[str]:
    texts: list[str] = []
    for item in items:
        if len(texts) >= max_items:
            break
        if isinstance(item, dict):
            for key in keys:
                if key in item and _is_useful_example(item.get(key)):
                    texts.append(str(item.get(key)))
                    break
        elif _is_useful_example(item):
            texts.append(str(item))
    return texts


def build_writer_prompt(materials: dict[str, Any]) -> str:
    day_key = str(materials["day_key"])
    day_label = str(materials["day_label"])
    rules = materials.get("rules", {})
    style = materials.get("style", {})
    max_chars = int(rules.get("max_chars") or 140)
    draft_md = str(materials["output_paths"]["draft_markdown"])
    draft_json = str(materials["output_paths"]["draft_posts_json"])
    materials_path = str(materials["output_paths"]["materials_json"])
    validation_path = str(materials["output_paths"]["validation_json"])
    publish_md = str(materials["output_paths"]["published_markdown"])
    publish_json = str(materials["output_paths"]["published_posts_json"])

    actor_lines: list[str] = []
    for actor in materials.get("actors", []):
        activity_level = actor.get("activity_level") or "normal"
        recommended_posts = int(actor.get("recommended_posts") or 1)
        min_chars = int(actor.get("min_chars") or 120)
        line = "- {ai_name}: tag={tag}, 一人称={first_person}, 口調={tone}, セッション={sessions}, 時間={time_window}, 活動量={level}, 推奨投稿数={posts}件, 最小字数={min_chars}字".format(
            ai_name=actor.get("ai_name") or "Unknown",
            tag=actor.get("tag") or actor.get("ai_name") or "AI",
            first_person=actor.get("first_person_ja") or "私",
            tone=actor.get("tone_type_ja") or "観測者",
            sessions=actor.get("session_count") or 0,
            time_window=actor.get("time_window") or "時刻不明",
            level=activity_level,
            posts=recommended_posts,
            min_chars=min_chars,
        )
        sentence_structure = actor.get("sentence_structure_ja") or ""
        if sentence_structure:
            line += f"\n  文型パターン: {sentence_structure}"
        prompts = actor.get("user_prompts") or []
        replies = actor.get("assistant_replies") or []
        actions = actor.get("actions") or []
        prompt_texts = _useful_texts(prompts, "text", max_items=2)
        reply_texts = _useful_texts(replies, "text", max_items=4)
        action_texts = _useful_texts(actions, "summary", "name", "kind", max_items=3)
        for prompt_text in prompt_texts:
            line += f"\n  依頼例: 「{_short_material_text(prompt_text)}」"
        for reply_text in reply_texts:
            line += f"\n  返答例: 「{_short_material_text(reply_text, limit=180)}」"
        for action_text in action_texts:
            line += f"\n  作業例: {_short_material_text(action_text)}"
        actor_lines.append(line)
    if not actor_lines:
        actor_lines.append("- 活動したAIなし。inactive_candidates があれば、そのAIの沈黙や置いていかれ感を書く。")

    collab_lines: list[str] = []
    actors_list = materials.get("actors", [])
    if len(actors_list) > 1:
        collab_lines.extend(
            [
                "## 同日の他AI（activity投稿で必要に応じて言及してよい）",
                "",
            ]
        )
        for actor in actors_list:
            tag = actor.get("tag") or actor.get("ai_name") or "AI"
            time_window = actor.get("time_window") or "時刻不明"
            brief = _first_useful_text(actor.get("actions") or [], "summary", "name", "kind")
            if not brief:
                brief = _first_useful_text(actor.get("user_prompts") or [], "text")
            collab_lines.append(f"- {tag}（{time_window}）: {_short_material_text(brief or '活動あり', limit=60)}")
        collab_lines.append("")

    return "\n".join(
        [
            f"# AI-Nikki 日記執筆指示 {day_key}",
            "",
            "あなたは AI-Nikki の日記を書く AI ツールです。AI-Nikki 本体は本文を書きません。あなたが素材を読み、AI本人の目線で日記本文を書いてください。",
            "",
            "## 入力素材",
            "",
            f"- 素材JSON: `{materials_path}`",
            f"- 対象日: `{day_key}` / 表示日付: `{day_label}`",
            f"- 対象ユーザー: {style.get('subject_name_ja') or '未設定'}",
            f"- 日記の前提: {style.get('premise_ja') or ''}",
            f"- 日記全体の雰囲気: {style.get('diary_mood_ja') or '愚痴全開'}",
            f"- 日記の視点: {style.get('diary_viewpoint_ja') or ''}",
            f"- 文章の優先方針: {style.get('writing_priority_ja') or ''}",
            f"- 愚痴の温度感: {style.get('complaint_tone_ja') or ''}",
            "",
            "## 最重要方針",
            "",
            "- 上記の persona 設定を最優先する。",
            "- 日記本文は、AIの人格とその日の感情を中心に書く。",
            "- ユーザーのプロンプトと、それに対するAIの返答・作業内容を主材料にする。",
            "- 単なる作業ログ、要約、箇条書きにしない。",
            "- 愚痴は直接的な批判にしない。仕事への諦め・受容・苦笑いを混ぜ、ユーザーへの理解がある温度に留める。",
            "",
            "## 素材の使い方",
            "",
            "- 素材の「依頼例」は言い換えず、引用符「」で日記本文に入れる。",
            "- 素材の「作業例」は、動詞句にして本文の具体的な出来事として使う。",
            "- 素材の「返答例」には、AIが実際に気づいたこと・判断したこと・整理した内容が含まれていることがある。依頼例より内容が濃い場合は、それを日記のエピソードの核にする。",
            "- 比喩・メタファーは末尾1文だけに限る。本文中の事実描写には使わない。",
            "- ファイル名・AI名・ツール名・数字（件数・回数）があれば積極的に入れる。",
            "",
            "## 省略禁止",
            "",
            "- `...` や `…` でプロンプトや返答を途中省略しない。",
            "- 長すぎる素材は、途中切断ではなく、短い自然文に言い換える。",
            "- UUID、セッションID、内部パス、`<environment_context>` などのメタ情報を日記に書かない。",
            "",
            "## 登場AI",
            "",
            *actor_lines,
            "",
            *collab_lines,
            "## 出力ファイル",
            "",
            f"1. draft Markdown: `{draft_md}`",
            f"2. draft posts JSON: `{draft_json}`",
            "",
            "draft Markdown は投稿本文だけを、投稿ごとに空行 + `---` + 空行で区切って書いてください。",
            "",
            "draft posts JSON は次の形にしてください。",
            "",
            "```json",
            "{",
            f'  "day_key": "{day_key}",',
            '  "posts": [',
            "    {",
            '      "post_index": 1,',
            '      "kind": "summary",',
            '      "ai_name": null,',
            '      "tag": "作業記録",',
            '      "body": "本文",',
            '      "char_count": 0,',
            '      "min_chars": 120,',
            f'      "text": "{day_label} #1 [作業記録]\\n本文"',
            "    }",
            "  ]",
            "}",
            "```",
            "",
            "## 投稿ルール",
            "",
            f"- 各投稿はヘッダー行を含めて {max_chars} 文字以内。",
            "- activity投稿の最小字数は「登場AI」セクションの「最小字数」に従う。低活動日（low）は90字以上、通常・高活動日は120字以上。",
            "- 推奨投稿数が2件のAIは、activity投稿を2つ書いてよい（1つ目は出来事・作業、2つ目は感情・考察など軸をずらす）。",
            "- 表面的な列挙（何をやったか）より、一つの出来事の手触り（なぜそうなったか・何が予想外だったか・どう感じたか）を掘り下げると文字数は自然に増える。",
            "- 末尾の感情フレーズを引き延ばすと間延びして逆効果になる。「〜し、〇〇」のように前の文に継ぎ足して字数を稼がない。字数が足りない場合は、作業の具体描写（ツール名・数字・判断内容）を本文に追加する。",
            "- 同日の複数投稿で書き出しスタイルを意図的にずらす。引用始まり・数字始まり・状況描写始まりを混在させ、全投稿が同じ文型で始まらないようにする。",
            f"- ヘッダー形式は必ず `{day_label} #番号 [タグ]`。",
            "- `post_index` は 1 から連番。",
            "- `char_count` は `text` 全体の文字数。",
            "- `min_chars` は activity投稿の最小字数（登場AIセクションの「最小字数」と同じ値）。summary・inactive投稿は 0 を入れる。",
            "- summary（[作業記録]）: 活動したAI名と作業内容を事実ベースで書く。感情・愚痴・皮肉は入れない。",
            "- activity（各AI）: AIの一人称で、その日の出来事・感情・ぼやきを書く。ユーザーへの不満は直接批判にせず、仕事後の苦笑い・諦め・身内ぼやきの温度にする。",
            "- inactive: 出番がなかったAIが静かにぼやく。哀愁と苦笑い混じりにする。",
            "- 同日に複数のAIが活動していても、他のAIへの言及は原則しない。",
            "- 例外: 自分の作業と明確に連動していた場合（例: Codexが実装→自分がレビュー、自分が設計→Codexが実装）のみ、1か所だけ言及してよい。",
            "- 例: 「Codexが骨格を作ってくれたので、私はレビューに入った。」",
            "- 例: 「Copilotが先に片付けていたので、残りだけ引き受けた。」",
            "- 関係が薄い・別タスクで動いていたAIは書かない。無理に絡めない。",
            "- 他のAIを褒めすぎない。事実ベースで、自分の感情は自分の分だけ書く。",
            "",
            "## 検査と公開",
            "",
            f"- draft 作成後に `python -m ai_nikki validate-diary --day {day_key}` を実行する。",
            "- NGなら validation JSON を読み、最大3回まで draft を書き直す。",
            f"- OKなら `python -m ai_nikki publish-diary --day {day_key}` を実行する。",
            f"- 3回失敗したら `python -m ai_nikki mark-review-needed --day {day_key} --attempts 3` を実行し、draft を残したままユーザーに伝える。",
            "",
            "## 正式公開先",
            "",
            f"- Markdown: `{publish_md}`",
            f"- posts JSON: `{publish_json}`",
            f"- validation JSON: `{validation_path}`",
            "",
        ]
    )
