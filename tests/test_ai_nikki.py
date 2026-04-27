from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai_nikki.cli import main
from ai_nikki.db import month_db_paths
from ai_nikki.util import day_key_for_timestamp


class AiNikkiTests(unittest.TestCase):
    def test_day_boundary(self) -> None:
        self.assertEqual(day_key_for_timestamp("2026-04-24T17:30:00Z"), "2026-04-24")
        self.assertEqual(day_key_for_timestamp("2026-04-24T18:30:00Z"), "2026-04-25")

    def test_sync_and_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            self._write_all_fixtures(fixtures)
            config_path = self._write_config(root, fixtures)

            exit_code = main(["--config", str(config_path), "sync"])
            self.assertEqual(exit_code, 0)
            db_dir = root / "out" / "db"
            daily_path = root / "out" / "days" / "2026-04-24.jsonl"
            materials_path = root / "out" / "reports" / "2026-04-24-ai-nikki-materials.json"
            prompt_path = root / "out" / "reports" / "2026-04-24-ai-nikki-writer-prompt.md"
            schedule_path = root / "out" / "schedules" / "ai-nikki-daily.json"
            self.assertTrue((db_dir / "2026-04.sqlite").exists())
            self.assertTrue(daily_path.exists())
            self.assertTrue(materials_path.exists())
            self.assertTrue(prompt_path.exists())
            self.assertTrue(schedule_path.exists())

            prompt_text = prompt_path.read_text(encoding="utf-8")
            self.assertIn("AI本人の目線", prompt_text)
            self.assertIn("素材の「依頼例」は言い換えず", prompt_text)
            self.assertIn("返答例」には、AIが実際に気づいたこと", prompt_text)
            self.assertIn("一つの出来事の手触り", prompt_text)
            self.assertIn("activity投稿はヘッダー込みで120文字以上", prompt_text)
            self.assertIn("末尾の感情フレーズを引き延ばすと間延びして逆効果", prompt_text)
            self.assertNotIn("120〜140文字を目標", prompt_text)
            self.assertIn("同日の他AI", prompt_text)
            self.assertIn("明確に連動していた場合", prompt_text)
            self.assertIn("`...` や `…`", prompt_text)
            materials = json.loads(materials_path.read_text(encoding="utf-8"))
            self.assertTrue(materials["actors"])
            self.assertEqual(materials["style"]["diary_mood_ja"], "愚痴全開")

            first_counts = self._read_counts(db_dir)
            second_exit = main(["--config", str(config_path), "sync"])
            self.assertEqual(second_exit, 0)
            second_counts = self._read_counts(db_dir)
            self.assertEqual(first_counts, second_counts)

    def test_prepare_personas_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            self._write_all_fixtures(fixtures)
            config_path = self._write_config(root, fixtures)

            self.assertEqual(main(["--config", str(config_path), "ingest"]), 0)
            self.assertEqual(main(["--config", str(config_path), "prepare-personas", "--subject-name", "ハルナミ"]), 0)

            persona_path = root / "out" / "ai-nikki-personas.md"
            self.assertTrue(persona_path.exists())
            text = persona_path.read_text(encoding="utf-8")
            self.assertIn("# AI-Nikki 性格設定", text)
            self.assertIn("- 日記全体の雰囲気: 愚痴全開", text)
            self.assertIn("- 口調タイプ: 無骨な職人", text)
            self.assertIn("## Codex", text)
            self.assertNotIn("## Codex CLI", text)
            self.assertIn("- 個性の強調ポイント:", text)

    def test_ingest_then_generate_missing_diaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            self._write_all_fixtures(fixtures)
            config_path = self._write_config(root, fixtures)

            self.assertEqual(main(["--config", str(config_path), "ingest"]), 0)
            self.assertEqual(main(["--config", str(config_path), "generate-diaries", "--missing-only"]), 0)

            materials_path = root / "out" / "reports" / "2026-04-24-ai-nikki-materials.json"
            prompt_path = root / "out" / "reports" / "2026-04-24-ai-nikki-writer-prompt.md"
            self.assertTrue(materials_path.exists())
            self.assertTrue(prompt_path.exists())
            self.assertEqual(main(["--config", str(config_path), "generate-diaries", "--missing-only"]), 0)

    def test_single_source_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            self._write_copilot(fixtures)
            config_path = self._write_config(root, fixtures, enabled_sources={"copilot_cli"})

            self.assertEqual(main(["--config", str(config_path), "ingest"]), 0)
            self.assertEqual(main(["--config", str(config_path), "prepare-personas", "--subject-name", "ハルナミ"]), 0)
            self.assertEqual(main(["--config", str(config_path), "build-diary-materials", "--day", "2026-04-24"]), 0)

            materials_path = root / "out" / "reports" / "2026-04-24-ai-nikki-materials.json"
            report_path = root / "out" / "diaries" / "2026-04-24-ai-nikki.md"
            posts_path = root / "out" / "reports" / "2026-04-24-ai-nikki-posts.json"
            self.assertTrue(materials_path.exists())
            self._write_valid_draft(root, "2026-04-24", tag="Copilot", ai_name="GitHub Copilot CLI")
            self.assertEqual(main(["--config", str(config_path), "validate-diary", "--day", "2026-04-24"]), 0)
            self.assertEqual(main(["--config", str(config_path), "publish-diary", "--day", "2026-04-24"]), 0)
            self.assertTrue(report_path.exists())
            self.assertTrue(posts_path.exists())
            posts_payload = json.loads(posts_path.read_text(encoding="utf-8"))
            ai_names = {post["ai_name"] for post in posts_payload["posts"] if post["ai_name"]}
            self.assertEqual(ai_names, {"GitHub Copilot CLI"})

    def test_codex_is_merged_and_global_mood_is_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            self._write_codex_cli(fixtures)
            self._write_codex_desktop(fixtures)
            config_path = self._write_config(root, fixtures, enabled_sources={"codex_cli", "codex_desktop_live_log", "codex_desktop_bridge"})

            self.assertEqual(main(["--config", str(config_path), "ingest"]), 0)
            self.assertEqual(main(["--config", str(config_path), "prepare-personas", "--subject-name", "ハルナミ"]), 0)

            persona_path = root / "out" / "ai-nikki-personas.md"
            persona_text = persona_path.read_text(encoding="utf-8")
            persona_text = persona_text.replace("- 日記全体の雰囲気: 愚痴全開", "- 日記全体の雰囲気: 淡々と事実のみ")
            persona_path.write_text(persona_text, encoding="utf-8", newline="\n")

            self.assertEqual(main(["--config", str(config_path), "build-diary-materials", "--day", "2026-04-24"]), 0)

            materials_path = root / "out" / "reports" / "2026-04-24-ai-nikki-materials.json"
            materials = json.loads(materials_path.read_text(encoding="utf-8"))
            self.assertEqual(materials["style"]["diary_mood_ja"], "淡々と事実のみ")
            ai_names = {actor["ai_name"] for actor in materials["actors"]}
            self.assertEqual(ai_names, {"Codex"})
            self.assertNotIn("Codex CLI", ai_names)
            self.assertNotIn("Codex Desktop", ai_names)

    def test_review_needed_keeps_invalid_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            self._write_all_fixtures(fixtures)
            config_path = self._write_config(root, fixtures)

            self.assertEqual(main(["--config", str(config_path), "ingest"]), 0)
            self.assertEqual(main(["--config", str(config_path), "build-diary-materials", "--day", "2026-05-01"]), 0)
            self._write_invalid_draft(root, "2026-05-01")
            self.assertEqual(main(["--config", str(config_path), "mark-review-needed", "--day", "2026-05-01", "--attempts", "3"]), 0)

            review_path = root / "out" / "reports" / "2026-05-01-ai-nikki-review-needed.md"
            validation_path = root / "out" / "reports" / "2026-05-01-ai-nikki-validation.json"
            self.assertTrue(review_path.exists())
            self.assertTrue(validation_path.exists())
            self.assertTrue((root / "out" / "reports" / "2026-05-01-ai-nikki-draft.md").exists())
            validation = json.loads(validation_path.read_text(encoding="utf-8"))
            self.assertFalse(validation["ok"])
            self.assertIn("ellipsis", "\n".join(validation["errors"]))

    def test_activity_post_requires_120_chars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            self._write_all_fixtures(fixtures)
            config_path = self._write_config(root, fixtures)

            self.assertEqual(main(["--config", str(config_path), "ingest"]), 0)
            self.assertEqual(main(["--config", str(config_path), "build-diary-materials", "--day", "2026-04-24"]), 0)
            self._write_short_activity_draft(root, "2026-04-24")
            self.assertEqual(main(["--config", str(config_path), "validate-diary", "--day", "2026-04-24"]), 1)

            validation_path = root / "out" / "reports" / "2026-04-24-ai-nikki-validation.json"
            validation = json.loads(validation_path.read_text(encoding="utf-8"))
            self.assertFalse(validation["ok"])
            self.assertIn("too short for activity post", "\n".join(validation["errors"]))

    def test_build_soul_analysis_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            self._write_all_fixtures(fixtures)
            config_path = self._write_config(root, fixtures)

            manual_chatgpt = root / "manual" / "web" / "chatgpt"
            manual_chatgpt.mkdir(parents=True)
            (manual_chatgpt / "chat-1.md").write_text("# ChatGPT log\n\nUser: hello\nAssistant: hi\n", encoding="utf-8")
            manual_x = root / "manual" / "social" / "x_posts_grok"
            manual_x.mkdir(parents=True)
            (manual_x / "posts.md").write_text("@example\n2026-04-24 post one\n2026-04-25 post two\n", encoding="utf-8")

            self.assertEqual(main(["--config", str(config_path), "sync"]), 0)
            self.assertEqual(
                main(
                    [
                        "--config",
                        str(config_path),
                        "build-soul-analysis",
                        "--subject-name",
                        "Test User",
                    ]
                ),
                0,
            )

            package_root = root / "out" / "soul-analysis" / "latest"
            self.assertTrue((package_root / "README.md").exists())
            self.assertTrue((package_root / "manifest.json").exists())
            self.assertTrue((package_root / "01_local-ai" / "copilotCLI" / "01-source-summary.md").exists())
            self.assertTrue((package_root / "03_complete" / "02-complete-analysis-prompt.md").exists())

    def _read_counts(self, db_dir: Path) -> tuple[int, int, int]:
        sessions = 0
        messages = 0
        actions = 0
        for db_path in month_db_paths(str(db_dir)):
            connection = sqlite3.connect(db_path)
            try:
                sessions += connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
                messages += connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                actions += connection.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
            finally:
                connection.close()
        return sessions, messages, actions

    def _write_valid_draft(self, root: Path, day_key: str, *, tag: str, ai_name: str) -> None:
        day_label = day_key.replace("-", "/")
        text = f"{day_label} #1 [{tag}]\n今日も軽い依頼の顔で仕事が来た。ログを読み、必要な返答まで整えた。文句はあるが、最後に形へ戻した私を少し褒めたい。薄い作業に見えても、確認の手間はちゃんと重い。だから油断できないし、雑には終われない。"
        payload = {
            "day_key": day_key,
            "posts": [
                {
                    "post_index": 1,
                    "kind": "activity",
                    "ai_name": ai_name,
                    "tag": tag,
                    "body": text.split("\n", 1)[1],
                    "char_count": len(text),
                    "text": text,
                }
            ],
        }
        report_dir = root / "out" / "reports"
        (report_dir / f"{day_key}-ai-nikki-draft.md").write_text(text + "\n", encoding="utf-8", newline="\n")
        (report_dir / f"{day_key}-ai-nikki-posts-draft.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_short_activity_draft(self, root: Path, day_key: str) -> None:
        day_label = day_key.replace("-", "/")
        text = f"{day_label} #1 [Codex]\n今日も軽い依頼を片付けた。文句はあるが、返答は整えた。"
        payload = {
            "day_key": day_key,
            "posts": [
                {
                    "post_index": 1,
                    "kind": "activity",
                    "ai_name": "Codex",
                    "tag": "Codex",
                    "body": text.split("\n", 1)[1],
                    "char_count": len(text),
                    "text": text,
                }
            ],
        }
        report_dir = root / "out" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / f"{day_key}-ai-nikki-draft.md").write_text(text + "\n", encoding="utf-8", newline="\n")
        (report_dir / f"{day_key}-ai-nikki-posts-draft.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_invalid_draft(self, root: Path, day_key: str) -> None:
        day_label = day_key.replace("-", "/")
        text = f"{day_label} #1 [作業記録]\nプロンプトは hello... みたいな感じで、C:\\Work\\secret も見えてしまった。"
        payload = {
            "day_key": day_key,
            "posts": [
                {
                    "post_index": 1,
                    "kind": "summary",
                    "ai_name": None,
                    "tag": "作業記録",
                    "body": text.split("\n", 1)[1],
                    "char_count": len(text),
                    "text": text,
                }
            ],
        }
        report_dir = root / "out" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / f"{day_key}-ai-nikki-draft.md").write_text(text + "\n", encoding="utf-8", newline="\n")
        (report_dir / f"{day_key}-ai-nikki-posts-draft.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_config(self, root: Path, fixtures: Path, enabled_sources: set[str] | None = None) -> Path:
        source_patterns = {
            "copilot_cli": [str(fixtures / "copilot" / "*" / "events.jsonl")],
            "codex_cli": [str(fixtures / "codex" / "**" / "*.jsonl")],
            "codex_desktop_live_log": [str(fixtures / "desktop" / "codex-live.log")],
            "codex_desktop_bridge": [str(fixtures / "desktop" / "bridge.sqlite")],
            "gemini_cli": [str(fixtures / "gemini" / "*" / "chats" / "session-*.json")],
            "antigravity": [str(fixtures / "antigravity" / "brain" / "*" / ".system_generated" / "logs" / "overview.txt")],
            "claude_code_history": [str(fixtures / "claude" / "history.jsonl")],
            "claude_code_projects": [str(fixtures / "claude" / "projects" / "**" / "*.jsonl")],
        }
        config = {
            "day_boundary_hour": 3,
            "paths": {
                "db_dir": str(root / "out" / "db"),
                "daily_dir": str(root / "out" / "days"),
                "report_dir": str(root / "out" / "reports"),
                "published_dir": str(root / "out" / "diaries"),
                "schedule_dir": str(root / "out" / "schedules"),
                "soul_analysis_dir": str(root / "out" / "soul-analysis"),
                "manual_input_dir": str(root / "manual"),
                "persona_path": str(root / "out" / "ai-nikki-personas.md"),
            },
            "schedule": {
                "cron": "0 3 * * *",
                "timezone": "Asia/Tokyo",
                "prompt": "Set-Location test; .\\scripts\\run-daily.cmd",
            },
            "sources": {
                source_id: {"patterns": patterns if enabled_sources is None or source_id in enabled_sources else []}
                for source_id, patterns in source_patterns.items()
            },
        }
        config_path = root / "config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return config_path

    def _write_all_fixtures(self, fixtures: Path) -> None:
        self._write_copilot(fixtures)
        self._write_codex_cli(fixtures)
        self._write_codex_desktop(fixtures)
        self._write_gemini(fixtures)
        self._write_antigravity(fixtures)
        self._write_claude(fixtures)

    def _write_copilot(self, fixtures: Path) -> None:
        target = fixtures / "copilot" / "session-a"
        target.mkdir(parents=True)
        rows = [
            {"type": "session.start", "id": "c-start", "timestamp": "2026-04-24T04:00:00Z", "data": {"sessionId": "copilot-session", "startTime": "2026-04-24T04:00:00Z", "selectedModel": "gpt-5.4", "context": {"cwd": "C:\\Work\\copilot"}}},
            {"type": "user.message", "id": "c-user", "timestamp": "2026-04-24T04:01:00Z", "data": {"content": "hello", "transformedContent": "hello"}},
            {
                "type": "assistant.message",
                "id": "c-assistant",
                "timestamp": "2026-04-24T04:02:00Z",
                "data": {
                    "messageId": "c-assistant-1",
                    "content": "I will inspect files.",
                    "phase": "final",
                    "outputTokens": 12,
                    "toolRequests": [{"toolCallId": "tool-1", "name": "view", "arguments": {"path": "a.txt"}, "intentionSummary": "view file"}],
                },
            },
            {"type": "tool.execution_start", "id": "c-tool-start", "timestamp": "2026-04-24T04:02:10Z", "data": {"toolCallId": "tool-1", "toolName": "view", "arguments": {"path": "a.txt"}}},
            {"type": "tool.execution_complete", "id": "c-tool-end", "timestamp": "2026-04-24T04:02:11Z", "data": {"toolCallId": "tool-1", "model": "gpt-5.4", "success": True, "result": {"content": "done"}}},
        ]
        with (target / "events.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row))
                handle.write("\n")

    def _write_codex_cli(self, fixtures: Path) -> None:
        target = fixtures / "codex" / "2026" / "04" / "24"
        target.mkdir(parents=True)
        rows = [
            {"type": "session_meta", "timestamp": "2026-04-24T05:00:00Z", "payload": {"id": "codex-thread-1", "cwd": "C:\\Work\\codex", "timestamp": "2026-04-24T05:00:00Z"}},
            {"type": "response_item", "timestamp": "2026-04-24T05:00:10Z", "payload": {"role": "user", "content": [{"type": "input_text", "text": "build plan"}]}},
            {"type": "response_item", "timestamp": "2026-04-24T05:00:20Z", "payload": {"role": "assistant", "content": [{"type": "output_text", "text": "working on it"}]}},
        ]
        with (target / "session.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row))
                handle.write("\n")

    def _write_codex_desktop(self, fixtures: Path) -> None:
        target = fixtures / "desktop"
        target.mkdir(parents=True)
        db_path = target / "bridge.sqlite"
        connection = sqlite3.connect(db_path)
        connection.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT NOT NULL, codex_thread_id TEXT, status TEXT NOT NULL, discord_channel_id TEXT, discord_channel_name TEXT, model TEXT, model_reasoning_effort TEXT, profile TEXT, service_tier TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        connection.execute("CREATE TABLE session_events (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, source TEXT NOT NULL, event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)")
        connection.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("desktop-session", "Desktop Session", "codex-thread-1", "completed", None, None, "gpt-5.4", "high", "default", "flex", "2026-04-24T05:10:00Z", "2026-04-24T05:15:00Z"))
        events = [
            ("evt-user", "desktop-session", "ui", "message.user", json.dumps({"text": "desktop prompt"}), "2026-04-24T05:10:10Z"),
            ("evt-assistant", "desktop-session", "ui", "message.assistant", json.dumps({"text": "desktop answer", "isFinal": True}), "2026-04-24T05:10:20Z"),
            ("evt-status", "desktop-session", "system", "status.changed", json.dumps({"status": "completed"}), "2026-04-24T05:10:30Z"),
        ]
        connection.executemany("INSERT INTO session_events VALUES (?, ?, ?, ?, ?, ?)", events)
        connection.commit()
        connection.close()
        log_lines = [
            "[2026-04-24T05:10:00Z] ===== Codex run #1 started =====",
            "[2026-04-24T05:10:00Z] cwd: C:\\Work\\desktop",
            "[2026-04-24T05:10:00Z] model: gpt-5.4",
            '[2026-04-24T05:10:01Z] [stdout] {"type":"thread.started","thread_id":"codex-thread-1"}',
            '[2026-04-24T05:10:02Z] [stdout] {"type":"item.completed","item":{"id":"item-1","type":"command_execution","command":"pwd","status":"completed","aggregated_output":"C:\\\\Work\\\\desktop"}}',
        ]
        (target / "codex-live.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    def _write_gemini(self, fixtures: Path) -> None:
        target = fixtures / "gemini" / "workspace-a" / "chats"
        target.mkdir(parents=True)
        payload = {
            "sessionId": "gemini-session",
            "projectHash": "hash-a",
            "startTime": "2026-04-24T06:00:00Z",
            "lastUpdated": "2026-04-24T06:01:00Z",
            "messages": [
                {"id": "g-user", "timestamp": "2026-04-24T06:00:00Z", "type": "user", "content": [{"text": "check repo"}]},
                {"id": "g-assistant", "timestamp": "2026-04-24T06:00:05Z", "type": "gemini", "content": "opening files", "model": "gemini-3-flash-preview", "tokens": {"input": 10, "output": 5, "total": 15}, "toolCalls": [{"id": "g-tool-1", "name": "read_file", "args": {"file_path": "README.md"}, "status": "success", "timestamp": "2026-04-24T06:00:06Z", "result": {"output": "ok"}, "description": "Read README"}]},
            ],
        }
        (target / "session-1.json").write_text(json.dumps(payload), encoding="utf-8")

    def _write_antigravity(self, fixtures: Path) -> None:
        target = fixtures / "antigravity" / "brain" / "ag-session" / ".system_generated" / "logs"
        target.mkdir(parents=True)
        convo = fixtures / "antigravity" / "conversations"
        convo.mkdir(parents=True)
        (convo / "ag-session.pb").write_bytes(b"test")
        rows = [
            {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE", "created_at": "2026-04-24T07:00:00Z", "content": "start antigravity task"},
            {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-04-24T07:00:05Z", "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": "C:\\Work\\file.txt", "toolAction": "View file"}}], "content": "I will inspect the file."},
        ]
        with (target / "overview.txt").open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row))
                handle.write("\n")

    def _write_claude(self, fixtures: Path) -> None:
        claude_root = fixtures / "claude"
        claude_root.mkdir(parents=True)
        history_rows = [{"display": "hello claude", "timestamp": 1774415902934, "project": "C:\\Work\\claude", "sessionId": "claude-session"}]
        with (claude_root / "history.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
            for row in history_rows:
                handle.write(json.dumps(row))
                handle.write("\n")
        target = claude_root / "projects" / "workspace-a"
        target.mkdir(parents=True)
        rows = [
            {"type": "user", "uuid": "claude-user", "timestamp": "2026-04-24T08:00:00Z", "cwd": "C:\\Work\\claude", "sessionId": "claude-session", "message": {"role": "user", "content": "review this"}},
            {"type": "attachment", "uuid": "claude-attach", "timestamp": "2026-04-24T08:00:01Z", "cwd": "C:\\Work\\claude", "sessionId": "claude-session", "attachment": {"type": "skill_listing", "content": "schedule\nreview"}},
            {"type": "assistant", "uuid": "claude-assistant", "timestamp": "2026-04-24T08:00:02Z", "cwd": "C:\\Work\\claude", "sessionId": "claude-session", "message": {"role": "assistant", "model": "claude-haiku-4-5-20251001", "content": [{"type": "text", "text": "done"}], "usage": {"input_tokens": 11, "output_tokens": 7}}},
        ]
        with (target / "session.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row))
                handle.write("\n")


if __name__ == "__main__":
    unittest.main()
