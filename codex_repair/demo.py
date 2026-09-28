"""Synthetic sample data. Never copies the user's configuration or history."""
from pathlib import Path
import json
import sqlite3
import sys

PARENT = "11111111-1111-4111-8111-111111111111"
FORK = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"
CURRENT = "44444444-4444-4444-8444-444444444444"


def create_demo(home: Path) -> dict:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text('''# 当前配置：保留登录和运行环境
model = "gpt-6-astra"
model_reasoning_effort = "xhigh"
notify = ["CURRENT_RUNTIME"]

[desktop]
followUpQueueMode = "queue"

[mcp_servers.node_repl]
command = "CURRENT_RUNTIME"

[shell_environment_policy.set]
DEMO_API_KEY = "FAKE_DEMO_CREDENTIAL_DO_NOT_USE"
''', encoding="utf-8")
    (home / "config.toml-bf").write_text('''model_provider = "codex"
model = "old-model"
disable_response_storage = true
personality = "pragmatic"
notify = ["OBSOLETE_RUNTIME"]

[model_providers.codex]
name = "Legacy demo provider"
base_url = "https://example.invalid/v1"
env_key = "DEMO_TOKEN"

[desktop]
localeOverride = "zh-CN"
followUpQueueMode = "interrupt"

[projects.'C:\\Demo\\WaterMonitor']
trust_level = "trusted"

[projects.'C:\\Demo\\SafetyPortal']
trust_level = "trusted"

[mcp_servers.node_repl]
command = "OBSOLETE_RUNTIME"

[mcp_servers.demo_database]
command = ''' + json.dumps(sys.executable) + '''
args = ["--version"]

[mcp_servers.demo_database.env]
MYSQL_PASS = "FAKE_DATABASE_PASSWORD"
''', encoding="utf-8")
    state = sqlite3.connect(home / "state_5.sqlite")
    state.execute("CREATE TABLE threads(id TEXT PRIMARY KEY,model_provider TEXT,rollout_path TEXT,title TEXT,created_at INTEGER,updated_at INTEGER,archived INTEGER,archived_at INTEGER)")
    history = sqlite3.connect(home / "thread_history_1.sqlite")
    history.executescript('''
    CREATE TABLE thread_turns(thread_id TEXT,turn_id TEXT,rollout_byte_offset INTEGER,rollout_end_byte_offset INTEGER,PRIMARY KEY(thread_id,turn_id));
    CREATE TABLE thread_history_projection_state(thread_id TEXT PRIMARY KEY,next_rollout_byte_offset INTEGER,next_rollout_ordinal INTEGER);
    CREATE TABLE thread_items(thread_id TEXT,item_id TEXT,item_json TEXT,PRIMARY KEY(thread_id,item_id));
    ''')
    paths = {}
    for physical_id, provider, archived, base in [
        (PARENT, "codex", False, None),
        (FORK, "codex", False, {"thread_id": PARENT, "end_ordinal_exclusive": 2, "end_byte_offset": 999}),
        (OTHER, "custom", True, None),
        (CURRENT, "openai", False, None),
    ]:
        folder = home / ("archived_sessions" if archived else "sessions/2026/09/01")
        folder.mkdir(parents=True, exist_ok=True)
        # A physical fork may retain the parent's public metadata ID.
        public_id = PARENT if physical_id == FORK else physical_id
        filename = f"rollout-2026-09-01T10-00-00-{physical_id}.jsonl"
        if physical_id == FORK:
            filename = f"rollout-2026-09-01T10-00-00-{PARENT}_{FORK}.jsonl"
        path = folder / filename
        payload = {"id": public_id, "session_id": public_id, "model_provider": provider, "history_mode": "paginated", "cwd": "C:\\Demo\\WaterMonitor"}
        if base:
            payload["history_base"] = base
        header = json.dumps({"timestamp": "2026-09-01T02:00:00Z", "type": "session_meta", "payload": payload}, separators=(",", ":")).encode() + b"\n"
        body = b'{"type":"response_item","payload":{"role":"assistant","text":"SAMPLE_HISTORY"}}\n'
        if physical_id == PARENT:
            body = body.replace(b"SAMPLE_HISTORY", b"x" * (999 - len(header) - len(body) + len(b"SAMPLE_HISTORY")))
            assert len(header + body) == 999
        path.write_bytes(header + body)
        paths[physical_id] = path
        if physical_id != FORK:
            state.execute("INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)", (physical_id, provider, str(path), "示例会话", 100, 200, int(archived), 201 if archived else None))
        history.execute("INSERT INTO thread_turns VALUES(?,?,?,?)", (physical_id, "turn-1", len(header), len(header + body)))
        history.execute("INSERT INTO thread_history_projection_state VALUES(?,?,?)", (physical_id, len(header + body), 2))
        history.execute("INSERT INTO thread_items VALUES(?,?,?)", (physical_id, "item-1", '{"text":"SAMPLE_HISTORY"}'))
    state.commit()
    history.commit()
    state.close()
    history.close()
    return paths
