from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .util import ensure_directory, redact_text, truncate_text


@dataclass(frozen=True)
class LocalSoulTarget:
    key: str
    display_name: str
    source_ids: tuple[str, ...]
    notes: str


@dataclass(frozen=True)
class WebSoulTarget:
    key: str
    display_name: str
    capture_tips: tuple[str, ...]
    manual_group: str = "web"
    mode: str = "conversation"


LOCAL_SOUL_TARGETS = (
    LocalSoulTarget(
        key="claudeCode",
        display_name="Claude Code",
        source_ids=("claude_code_projects", "claude_code_history"),
        notes="Claude Code project logs and history support files. History may overlap with project logs and should be treated as supplemental evidence.",
    ),
    LocalSoulTarget(
        key="copilotCLI",
        display_name="GitHub Copilot CLI",
        source_ids=("copilot_cli",),
        notes="Structured Copilot CLI session logs with user messages, assistant messages, and tool executions.",
    ),
    LocalSoulTarget(
        key="codexCLI",
        display_name="Codex CLI",
        source_ids=("codex_cli",),
        notes="Codex CLI transcript-style logs. Strong signal for exactness, routing checks, and CLI-driven implementation work.",
    ),
    LocalSoulTarget(
        key="codexDesktop",
        display_name="Codex Desktop",
        source_ids=("codex_desktop_bridge", "codex_desktop_live_log"),
        notes="Desktop bridge sessions plus live log action traces. Bridge is the main transcript source; live log mainly enriches action history.",
    ),
    LocalSoulTarget(
        key="geminiCLI",
        display_name="Gemini CLI",
        source_ids=("gemini_cli",),
        notes="Gemini CLI session JSON and log files, useful for wide multi-purpose usage patterns and completion-oriented requests.",
    ),
    LocalSoulTarget(
        key="antigravity",
        display_name="Antigravity",
        source_ids=("antigravity",),
        notes="Antigravity overview logs. Evidence quality depends on what the local overview files captured.",
    ),
)


WEB_SOUL_TARGETS = (
    WebSoulTarget(
        key="chatgpt",
        display_name="ChatGPT Web",
        capture_tips=(
            "Preferred: use any built-in data export or conversation export feature if your account offers it.",
            "Fallback: open the important chats in the browser and copy the transcript into one or more Markdown or text files.",
            "If you only have screenshots, convert them into text before analysis when possible.",
        ),
    ),
    WebSoulTarget(
        key="claude",
        display_name="Claude Web",
        capture_tips=(
            "Preferred: use any built-in export or account data download feature if available.",
            "Fallback: copy the chat transcript from the browser into Markdown or text files.",
            "If the chats are spread across projects, export or copy them project by project instead of mixing everything into one file without labels.",
        ),
    ),
    WebSoulTarget(
        key="gemini",
        display_name="Gemini Web",
        capture_tips=(
            "Preferred: use any official export or takeout-style data export option if available for your account.",
            "Fallback: copy the relevant chat transcript into Markdown or text files.",
            "If you used file uploads in Gemini, keep short notes about those attachments beside the copied transcript.",
        ),
    ),
    WebSoulTarget(
        key="grok",
        display_name="Grok Web",
        capture_tips=(
            "If Grok offers an export feature in your environment, use it.",
            "Otherwise, copy each relevant conversation into Markdown or text files.",
            "Keep one file per conversation when possible so the analysis AI can reason about session boundaries.",
        ),
    ),
    WebSoulTarget(
        key="x_posts_grok",
        display_name="X User Posts via Grok",
        capture_tips=(
            "Best source: the X account owner's own post export or archive, filtered to posts authored by that user.",
            "Good fallback: copy posts from the target profile page into Markdown or text files, keeping timestamps and links when possible.",
            "If using Grok directly, provide the X handle and attach or paste the exported post corpus so Grok can combine public-profile context with the actual text evidence.",
            "Separate original posts and replies if you can. If you keep replies, label them clearly.",
        ),
        manual_group="social",
        mode="x_posts",
    ),
)


SOUL_ANALYSIS_STRUCTURE = """# Required output structure

Use this structure in Japanese:

1. `0. 総評`
2. `Ⅰ. 文脈と意図の解読`
3. `Ⅱ. 文体的指紋（Stylistic Fingerprint）`
4. `Ⅲ. 思考アーキテクチャと認知スタイル`
5. `Ⅳ. 価値観のヒエラルキーと信念体系`
6. `Ⅴ. 感情的ドライブと動機`
7. `Ⅵ. 知識体系と専門性`
8. `Ⅶ. 影響関係と参照フレーム`
9. `Ⅷ. 文章再現のためのガイドライン【最重要】`
10. `UserProfile出力形式（最終まとめ）`

Within the analysis:
- Separate **確実 / おそらく / かもしれない** when confidence differs.
- Quote only short safe excerpts.
- Never expose secrets, tokens, personal addresses, or anything that looks credential-like.
- If evidence is thin, say so plainly instead of over-claiming.
"""


def build_soul_analysis_package(
    connection: sqlite3.Connection,
    config: dict[str, Any],
    *,
    subject_name: str,
    label: str = "latest",
    from_day: str | None = None,
    to_day: str | None = None,
) -> dict[str, Any]:
    root_dir = Path(config["paths"]["soul_analysis_dir"]) / label
    manual_root = Path(config["paths"]["manual_input_dir"])
    local_root = root_dir / "01_local-ai"
    web_root = root_dir / "02_web-ai"
    complete_root = root_dir / "03_complete"
    outputs_root = root_dir / "outputs"
    ensure_directory(root_dir)
    ensure_directory(local_root)
    ensure_directory(web_root)
    ensure_directory(complete_root)
    ensure_directory(outputs_root)
    ensure_directory(manual_root)

    local_results = [
        _write_local_target_bundle(
            connection,
            local_root,
            outputs_root,
            target,
            subject_name=subject_name,
            from_day=from_day,
            to_day=to_day,
        )
        for target in LOCAL_SOUL_TARGETS
    ]
    web_results = [
        _write_web_target_bundle(
            web_root,
            outputs_root,
            manual_root,
            target,
            subject_name=subject_name,
        )
        for target in WEB_SOUL_TARGETS
    ]
    _write_outputs_readme(outputs_root, local_results, web_results)
    status_path = _write_source_status(complete_root, outputs_root, local_results, web_results)
    complete_prompt_path = _write_complete_prompt(
        complete_root,
        outputs_root,
        subject_name=subject_name,
        local_results=local_results,
        web_results=web_results,
        status_path=status_path,
    )
    readme_path = _write_root_readme(
        root_dir,
        manual_root,
        subject_name=subject_name,
        label=label,
        local_results=local_results,
        web_results=web_results,
        status_path=status_path,
        complete_prompt_path=complete_prompt_path,
    )
    manifest = {
        "subject_name": subject_name,
        "label": label,
        "package_root": str(root_dir),
        "manual_input_root": str(manual_root),
        "from_day": from_day,
        "to_day": to_day,
        "local_targets": local_results,
        "web_targets": web_results,
        "complete_prompt": str(complete_prompt_path),
        "source_status": str(status_path),
        "readme": str(readme_path),
    }
    (root_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def _write_local_target_bundle(
    connection: sqlite3.Connection,
    local_root: Path,
    outputs_root: Path,
    target: LocalSoulTarget,
    *,
    subject_name: str,
    from_day: str | None,
    to_day: str | None,
) -> dict[str, Any]:
    target_dir = local_root / target.key
    ensure_directory(target_dir)
    messages = _fetch_messages(connection, target.source_ids, from_day=from_day, to_day=to_day)
    actions = _fetch_actions(connection, target.source_ids, from_day=from_day, to_day=to_day)
    user_messages = [row for row in messages if row["role"] == "user"]
    assistant_messages = [row for row in messages if row["role"] == "assistant"]
    models = sorted({row["model"] for row in messages if row["model"]})
    workspaces = sorted({row["workspace_path"] for row in messages if row["workspace_path"]})
    session_ids = sorted({row["session_uid"] for row in messages} | {row["session_uid"] for row in actions})
    top_actions = _count_top_actions(actions)
    output_path = outputs_root / f"Soul Analysis - {target.display_name}.md"
    prompt_path = target_dir / "03-analysis-prompt.md"
    summary_path = target_dir / "01-source-summary.md"
    prompts_path = target_dir / "02-user-prompts.jsonl"

    summary_lines = [
        f"# {target.display_name} Source Summary",
        "",
        f"- Subject: {subject_name}",
        f"- Source ids: {', '.join(target.source_ids)}",
        f"- Sessions: {len(session_ids)}",
        f"- User messages: {len(user_messages)}",
        f"- Assistant messages: {len(assistant_messages)}",
        f"- Actions: {len(actions)}",
        f"- Models: {', '.join(models) if models else '(none captured)'}",
        f"- Workspaces: {', '.join(workspaces[:12]) if workspaces else '(none captured)'}",
        f"- Day filter: {from_day or '(start)'} .. {to_day or '(end)'}",
        "",
        "## Notes",
        "",
        target.notes,
        "",
        "## Representative user prompts",
        "",
    ]
    if user_messages:
        for row in user_messages[:20]:
            summary_lines.append(
                f"- {row['ts']} [{row['source_id']}] {truncate_text(redact_text(row['content_text'] or ''), 220)}"
            )
    else:
        summary_lines.append("- No user messages were captured for this target.")
    summary_lines.extend(["", "## Representative assistant outputs", ""])
    if assistant_messages:
        for row in assistant_messages[:10]:
            summary_lines.append(
                f"- {row['ts']} [{row['source_id']}] {truncate_text(redact_text(row['content_text'] or ''), 220)}"
            )
    else:
        summary_lines.append("- No assistant messages were captured for this target.")
    summary_lines.extend(["", "## Top actions", "", "| Action | Count |", "| --- | ---: |"])
    if top_actions:
        for name, count in top_actions:
            summary_lines.append(f"| {name} | {count} |")
    else:
        summary_lines.append("| (none) | 0 |")
    summary_lines.extend(
        [
            "",
            "## Evidence files",
            "",
            f"- Full user prompts JSONL: `{prompts_path}`",
            f"- Prompt to run the actual Soul Analysis: `{prompt_path}`",
            f"- Expected output path: `{output_path}`",
        ]
    )
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8", newline="\n")

    with prompts_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in user_messages:
            payload = {
                "timestamp": row["ts"],
                "source_id": row["source_id"],
                "session_uid": row["session_uid"],
                "source_session_id": row["source_session_id"],
                "workspace_path": row["workspace_path"],
                "title": row["title"],
                "model": row["model"],
                "content_text": redact_text(row["content_text"] or ""),
            }
            handle.write(json.dumps(payload, ensure_ascii=False))
            handle.write("\n")

    prompt_lines = [
        f"# Soul Analysis Prompt - {target.display_name}",
        "",
        f"Goal: analyze **{subject_name}** from their `{target.display_name}` interaction logs and write one Markdown document.",
        "",
        "## Read these files first",
        "",
        f"1. `{summary_path}`",
        f"2. `{prompts_path}`",
        "",
        "## Output requirement",
        "",
        f"Write the result to: `{output_path}`",
        "",
        SOUL_ANALYSIS_STRUCTURE,
        "",
        "## Extra rules",
        "",
        "- Focus on the user's prompts, requests, constraints, preferences, and recurring decision patterns.",
        "- Use assistant outputs only as supporting context, not as the main subject.",
        "- Treat this source as one facet of the subject, not the whole person.",
        "- If the evidence is sparse, note what can and cannot be concluded.",
        "- Keep the analysis practical enough that it can feed a digital clone or writing-style profile later.",
    ]
    prompt_path.write_text("\n".join(prompt_lines) + "\n", encoding="utf-8", newline="\n")
    return {
        "key": target.key,
        "display_name": target.display_name,
        "source_ids": list(target.source_ids),
        "session_count": len(session_ids),
        "user_message_count": len(user_messages),
        "assistant_message_count": len(assistant_messages),
        "action_count": len(actions),
        "summary_path": str(summary_path),
        "user_prompts_path": str(prompts_path),
        "prompt_path": str(prompt_path),
        "expected_output_path": str(output_path),
    }


def _write_web_target_bundle(
    web_root: Path,
    outputs_root: Path,
    manual_root: Path,
    target: WebSoulTarget,
    *,
    subject_name: str,
) -> dict[str, Any]:
    target_dir = web_root / target.key
    ensure_directory(target_dir)
    manual_dir = manual_root / target.manual_group / target.key
    ensure_directory(manual_dir)
    _write_manual_readme(manual_dir, target)
    manual_files = sorted(path for path in manual_dir.rglob("*") if path.is_file() and path.name.lower() != "readme.md")
    output_path = outputs_root / f"Soul Analysis - {target.display_name}.md"
    guide_path = target_dir / "01-manual-log-guide.md"
    prompt_path = target_dir / "02-analysis-prompt.md"
    detected_path = target_dir / "03-detected-files.md"

    if target.mode == "x_posts":
        guide_lines = [
            f"# {target.display_name} Manual Source Guide",
            "",
            f"- Subject: {subject_name}",
            f"- Put exported or copied X-post files into: `{manual_dir}`",
            f"- Expected output path after analysis: `{output_path}`",
            "",
            "## What the human must do",
            "",
            "1. Gather **the target user's own X posts**. Prefer official export/archive files, but copied profile posts are acceptable.",
            "2. Save them into the folder above. Recommended formats: `.md`, `.txt`, `.json`, `.html`, `.csv`, `.pdf`.",
            "3. If possible, include the X handle, post timestamps, links, and whether each item is an original post or a reply.",
            "4. Re-run the package builder so the detected-files report refreshes.",
            "5. Open Grok, attach or paste the gathered post files, and paste the generated Grok prompt.",
            "6. Save Grok's returned Markdown into the expected output path.",
            "",
            "## Recommended collection methods",
            "",
            "1. **Official archive/export**: best for completeness and timestamps.",
            "2. **Profile copy**: open the target profile, copy the user's own posts into Markdown with links.",
            "3. **Mixed method**: use a profile copy for recent posts and an export for the full backlog.",
            "",
            "## Capture tips",
            "",
        ]
    else:
        guide_lines = [
            f"# {target.display_name} Manual Log Intake Guide",
            "",
            f"- Subject: {subject_name}",
            f"- Put exported or copied transcripts into: `{manual_dir}`",
            f"- Expected output path after analysis: `{output_path}`",
            "",
            "## What the human must do",
            "",
            "1. Gather the transcript or export files for this web AI.",
            "2. Save them into the folder above. Recommended formats: `.md`, `.txt`, `.json`, `.html`, `.pdf`.",
            "3. Re-run the package builder so the status files refresh.",
            "4. Open any capable AI tool, attach those files, and paste the generated analysis prompt.",
            "5. Save the returned Markdown into the expected output path.",
            "",
            "## Capture tips",
            "",
        ]
    for tip in target.capture_tips:
        guide_lines.append(f"- {tip}")
    guide_lines.extend(
        [
            "",
            "## Important guardrails",
            "",
            "- Do not mix multiple users into one transcript set.",
            "- Keep rough chronological order when possible.",
            "- If you redact something manually, note that it was redacted.",
            "- If export is impossible, a carefully copied transcript is still acceptable.",
            "- Preserve links and timestamps whenever you can.",
        ]
    )
    guide_path.write_text("\n".join(guide_lines) + "\n", encoding="utf-8", newline="\n")

    if target.mode == "x_posts":
        prompt_lines = [
            f"# Grok Soul Analysis Prompt - {target.display_name}",
            "",
            f"Goal: analyze **{subject_name}** from their own X posts and write one Markdown document.",
            "",
            "## Recommended AI",
            "",
            "- Preferred: **Grok**, because it is closest to the X context and can reason well about posting style, self-presentation, and audience framing.",
            "",
            "## Inputs",
            "",
            f"- Attach or paste the X-post files from: `{manual_dir}`",
            f"- You may also read the intake guide: `{guide_path}`",
            "- If known, include the X handle explicitly in the prompt.",
            "",
            "## Output requirement",
            "",
            f"Write the result to: `{output_path}`",
            "",
            SOUL_ANALYSIS_STRUCTURE,
            "",
            "## Extra rules",
            "",
            "- Analyze **only the target user's own posts** as the main signal.",
            "- Treat replies and repost commentary as optional secondary evidence unless they dominate the dataset.",
            "- Focus on public persona, recurring themes, rhetorical habits, emotional temperature, audience assumptions, and self-positioning.",
            "- If the post set is biased toward one period or one topic, call that out.",
            "- Never expose private data, email addresses, tokens, or non-public personal details.",
            "- Short quoted posts are allowed only when they materially support the analysis.",
        ]
    else:
        prompt_lines = [
            f"# Soul Analysis Prompt - {target.display_name}",
            "",
            f"Goal: analyze **{subject_name}** from their `{target.display_name}` conversation transcripts and write one Markdown document.",
            "",
            "## Inputs",
            "",
            f"- Attach or paste the transcript files from: `{manual_dir}`",
            f"- You may also read the intake guide: `{guide_path}`",
            "",
            "## Output requirement",
            "",
            f"Write the result to: `{output_path}`",
            "",
            SOUL_ANALYSIS_STRUCTURE,
            "",
            "## Extra rules",
            "",
            "- The transcript files may be pasted chat copies, official exports, or hand-cleaned notes.",
            "- If the logs are incomplete, call that out explicitly.",
            "- Focus on the user's behavior, constraints, values, writing style, and recurring intent.",
            "- Never expose secrets, access tokens, private links, or sensitive personal data in the report.",
            "- Short quotes are allowed only when they strengthen the evidence.",
        ]
    prompt_path.write_text("\n".join(prompt_lines) + "\n", encoding="utf-8", newline="\n")

    detected_lines = [
        f"# Detected files - {target.display_name}",
        "",
        f"- Manual input directory: `{manual_dir}`",
        f"- Files detected: {len(manual_files)}",
        "",
    ]
    if manual_files:
        detected_lines.extend(["## File list", ""])
        for path in manual_files:
            detected_lines.append(f"- `{path}` ({path.stat().st_size} bytes)")
    else:
        detected_lines.extend(
            [
                "No manual transcript files were found yet.",
                "",
                "Add files to the manual input directory, then run:",
                "",
                "```powershell",
                "python -m ai_nikki build-soul-analysis",
                "```",
            ]
        )
    detected_path.write_text("\n".join(detected_lines) + "\n", encoding="utf-8", newline="\n")
    return {
        "key": target.key,
        "display_name": target.display_name,
        "manual_dir": str(manual_dir),
        "manual_file_count": len(manual_files),
        "manual_files": [str(path) for path in manual_files],
        "guide_path": str(guide_path),
        "prompt_path": str(prompt_path),
        "detected_files_path": str(detected_path),
        "expected_output_path": str(output_path),
    }


def _write_outputs_readme(outputs_root: Path, local_results: list[dict[str, Any]], web_results: list[dict[str, Any]]) -> None:
    lines = [
        "# Soul Analysis Outputs",
        "",
        "Save the finished per-source analyses and the final complete analysis in this folder.",
        "",
        "## Expected per-source files",
        "",
    ]
    for result in [*local_results, *web_results]:
        lines.append(f"- `{result['expected_output_path']}`")
    lines.extend(["", "## Final synthesis", "", f"- `{outputs_root / 'Soul Analysis Complete.md'}`"])
    (outputs_root / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _write_source_status(
    complete_root: Path,
    outputs_root: Path,
    local_results: list[dict[str, Any]],
    web_results: list[dict[str, Any]],
) -> Path:
    path = complete_root / "01-source-status.md"
    lines = [
        "# Soul Analysis Source Status",
        "",
        "## Local AI packets",
        "",
        "| Source | User messages | Actions | Output exists |",
        "| --- | ---: | ---: | --- |",
    ]
    for result in local_results:
        exists = Path(result["expected_output_path"]).exists()
        lines.append(
            f"| {result['display_name']} | {result['user_message_count']} | {result['action_count']} | {'yes' if exists else 'no'} |"
        )
    lines.extend(["", "## Web AI manual sources", "", "| Source | Manual files | Output exists |", "| --- | ---: | --- |"])
    for result in web_results:
        exists = Path(result["expected_output_path"]).exists()
        lines.append(f"| {result['display_name']} | {result['manual_file_count']} | {'yes' if exists else 'no'} |")
    lines.extend(
        [
            "",
            "## Final complete analysis",
            "",
            f"- Expected path: `{outputs_root / 'Soul Analysis Complete.md'}`",
            f"- Exists: {'yes' if (outputs_root / 'Soul Analysis Complete.md').exists() else 'no'}",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def _write_complete_prompt(
    complete_root: Path,
    outputs_root: Path,
    *,
    subject_name: str,
    local_results: list[dict[str, Any]],
    web_results: list[dict[str, Any]],
    status_path: Path,
) -> Path:
    output_path = outputs_root / "Soul Analysis Complete.md"
    path = complete_root / "02-complete-analysis-prompt.md"
    available_inputs = [
        result["expected_output_path"]
        for result in [*local_results, *web_results]
        if Path(result["expected_output_path"]).exists()
    ]
    missing_inputs = [
        result["expected_output_path"]
        for result in [*local_results, *web_results]
        if not Path(result["expected_output_path"]).exists()
    ]
    lines = [
        "# Soul Analysis Complete Prompt",
        "",
        f"Goal: synthesize all available per-AI Soul Analysis files for **{subject_name}** into one detailed Markdown file.",
        "",
        "## Read first",
        "",
        f"- Source status: `{status_path}`",
        "",
        "## Available input files",
        "",
    ]
    if available_inputs:
        for file_path in available_inputs:
            lines.append(f"- `{file_path}`")
    else:
        lines.append("- No per-AI Soul Analysis files exist yet.")
    lines.extend(["", "## Missing input files", ""])
    if missing_inputs:
        for file_path in missing_inputs:
            lines.append(f"- `{file_path}`")
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Output requirement",
            "",
            f"Write the result to: `{output_path}`",
            "",
            "# Required structure",
            "",
            "1. `0. 結論`",
            "2. `1. ソースごとの信頼度`",
            "3. `2. あなたの一番深い欲求`",
            "4. `3. 二つ以上のモードや人格面の切り替え`",
            "5. `4. 思考アーキテクチャ`",
            "6. `5. 価値観の優先順位`",
            "7. `6. 強く嫌うもの / 絶対に譲れないもの`",
            "8. `7. 感情の動き`",
            "9. `8. 専門性の束ね方`",
            "10. `9. 矛盾と緊張点`",
            "11. `10. デジタルクローンとして再現すべき人格`",
            "12. `11. 最終プロフィール`",
            "13. `12. 模倣キーワード / 禁止表現`",
            "14. `13. 総括`",
            "",
            "## Synthesis rules",
            "",
            "- Make agreement and disagreement between sources explicit.",
            "- If public-web persona and local-work persona differ, treat that as a real pattern instead of flattening it away.",
            "- Weight sources by evidence quality, not by how dramatic they sound.",
            "- Never invent evidence that is not present in the per-AI analyses.",
            "- If some sources are missing, still complete the synthesis and state the gap plainly.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def _write_root_readme(
    root_dir: Path,
    manual_root: Path,
    *,
    subject_name: str,
    label: str,
    local_results: list[dict[str, Any]],
    web_results: list[dict[str, Any]],
    status_path: Path,
    complete_prompt_path: Path,
) -> Path:
    path = root_dir / "README.md"
    lines = [
        "# Soul Analysis Workflow Package",
        "",
        f"- Subject: {subject_name}",
        f"- Package label: {label}",
        f"- Package root: `{root_dir}`",
        f"- Manual input root: `{manual_root}`",
        "",
        "## What AI already did for you",
        "",
        "1. Packaged local PC-side AI logs by source.",
        "2. Wrote one analysis prompt per local AI source.",
        "3. Created manual intake guides for web AI logs.",
        "4. Prepared the final synthesis prompt for `Soul Analysis Complete`.",
        "",
        "## What a human still needs to do",
        "",
        "1. Gather web AI transcripts and put them in the manual input folders.",
        "2. If your current AI tool cannot directly access a browser export, upload those files manually to the AI you use for analysis.",
        "3. Save each finished per-AI analysis into the `outputs` folder.",
        "",
        "## Recommended universal workflow",
        "",
        "1. Run log sync first.",
        "   - `python -m ai_nikki sync`",
        "2. Build or refresh this package.",
        "   - `python -m ai_nikki build-soul-analysis --subject-name \"Your Name\"`",
        "3. For each local AI packet under `01_local-ai`, open `03-analysis-prompt.md` and let your current AI tool create the actual Soul Analysis file.",
        "4. For each web AI packet under `02_web-ai`, follow `01-manual-log-guide.md`, then use `02-analysis-prompt.md` with the gathered transcript files.",
        "5. Check status in the complete folder, then run the final synthesis using the complete prompt.",
        "",
        "## Tool-agnostic invocation examples",
        "",
        "- Copilot CLI: `Run AI-Nikki sync, then build the soul analysis package, then create the local per-AI Soul Analysis files from the generated prompts.`",
        "- Claude Code: `Use the AI-Nikki soul-analysis workflow package in this repo. Run sync if needed, build the package, then execute the local source prompts and tell me what manual web-log steps remain.`",
        "- Gemini CLI: `In this AI-Nikki repo, build the Soul Analysis workflow package and process every local AI packet you can from the generated prompts.`",
        "",
        "## Current source status",
        "",
        f"- `{status_path}`",
        f"- `{complete_prompt_path}`",
        "",
        "## Local packet inventory",
        "",
    ]
    for result in local_results:
        lines.append(
            f"- {result['display_name']}: `{result['summary_path']}` / `{result['user_prompts_path']}` / `{result['prompt_path']}`"
        )
    lines.extend(["", "## Web packet inventory", ""])
    for result in web_results:
        lines.append(
            f"- {result['display_name']}: `{result['guide_path']}` / `{result['prompt_path']}` / `{result['detected_files_path']}`"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def _write_manual_readme(manual_dir: Path, target: WebSoulTarget) -> None:
    path = manual_dir / "README.md"
    if path.exists():
        return
    if target.mode == "x_posts":
        lines = [
            f"# Manual input folder - {target.display_name}",
            "",
            "Put exported or copied **X posts authored by the target user** here.",
            "",
            "Recommended formats:",
            "- Markdown (`.md`)",
            "- Plain text (`.txt`)",
            "- JSON export (`.json`)",
            "- CSV (`.csv`)",
            "- HTML export (`.html`)",
            "- PDF export (`.pdf`) if you cannot get text",
            "",
            "Keep timestamps, links, and the X handle when possible.",
            "If replies are mixed in, label them clearly.",
        ]
    else:
        lines = [
            f"# Manual input folder - {target.display_name}",
            "",
            "Put exported or copied transcript files for this web AI here.",
            "",
            "Recommended formats:",
            "- Markdown (`.md`)",
            "- Plain text (`.txt`)",
            "- JSON export (`.json`)",
            "- HTML export (`.html`)",
            "- PDF export (`.pdf`) if you cannot get text",
            "",
            "One conversation per file is usually the easiest to analyze later.",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _fetch_messages(
    connection: sqlite3.Connection,
    source_ids: tuple[str, ...],
    *,
    from_day: str | None,
    to_day: str | None,
) -> list[sqlite3.Row]:
    placeholders = ", ".join("?" for _ in source_ids)
    where = [f"m.source_id IN ({placeholders})"]
    params: list[Any] = list(source_ids)
    if from_day:
        where.append("m.day_key >= ?")
        params.append(from_day)
    if to_day:
        where.append("m.day_key <= ?")
        params.append(to_day)
    query = f"""
        SELECT
          m.message_uid,
          m.session_uid,
          m.source_id,
          m.source_message_id,
          m.ts,
          m.day_key,
          m.seq,
          m.role,
          m.model,
          m.content_text,
          s.source_session_id,
          s.workspace_path,
          s.title
        FROM messages m
        JOIN sessions s ON s.session_uid = m.session_uid
        WHERE {' AND '.join(where)}
        ORDER BY m.ts, m.seq
    """
    return connection.execute(query, params).fetchall()


def _fetch_actions(
    connection: sqlite3.Connection,
    source_ids: tuple[str, ...],
    *,
    from_day: str | None,
    to_day: str | None,
) -> list[sqlite3.Row]:
    placeholders = ", ".join("?" for _ in source_ids)
    where = [f"a.source_id IN ({placeholders})"]
    params: list[Any] = list(source_ids)
    if from_day:
        where.append("a.day_key >= ?")
        params.append(from_day)
    if to_day:
        where.append("a.day_key <= ?")
        params.append(to_day)
    query = f"""
        SELECT
          a.action_uid,
          a.session_uid,
          a.source_id,
          a.ts,
          a.seq,
          a.kind,
          a.name,
          a.summary
        FROM actions a
        WHERE {' AND '.join(where)}
        ORDER BY a.ts, a.seq
    """
    return connection.execute(query, params).fetchall()


def _count_top_actions(actions: list[sqlite3.Row]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for row in actions:
        name = row["name"] or row["kind"] or "(unknown)"
        counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:12]
