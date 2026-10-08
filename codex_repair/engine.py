from __future__ import annotations

import copy
import difflib
import hashlib
import json
import os
import re
import shutil
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit
from uuid import uuid4

import psutil
import tomlkit

FORMAT = "codex-config-repair/v1"
BACKUP_DIR = "repair-backups"
MAX_HEADER = 8 * 1024 * 1024
MAX_CONFIG = 2 * 1024 * 1024
UUID_END = re.compile(r"([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\.jsonl$")
RUNTIME_SERVERS = {"node_repl", "cua_repl"}
RELAY_PROVIDER = "codex_repair_relay"
SECRET = re.compile(r"api.?key|token|secret|pass(?:word)?|authorization|credential|cookie|bearer", re.I)


class RepairError(Exception):
    """A recoverable error suitable for display without secret-bearing raw input."""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path, body: bool = False) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        if body:
            stream.readline()
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute() or relative_path.drive or ".." in relative_path.parts:
        raise RepairError("备份中包含无效路径，已停止操作。")
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise RepairError("文件路径超出所选目录，已停止操作。")
    return path


def default_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()


def running_codex() -> list[str]:
    found = []
    for process in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = (process.info["name"] or "").lower()
            executable = (process.info["exe"] or "").lower()
            if name in {"codex", "codex.exe"} or (
                name in {"chatgpt", "chatgpt.exe", "codex.app"}
                and (not executable or "openai" in executable or "codex" in executable or "chatgpt" in executable)
            ):
                found.append(f"{process.info['name']} · PID {process.info['pid']}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def require_closed(probe: Callable[[], list[str]]) -> None:
    if probe():
        raise RepairError("请先退出 Codex 桌面程序和 Codex 命令行，再执行修复或恢复。扫描不受影响。")


@contextmanager
def operation_lock(home: Path):
    path = home / ".config-repair.lock"
    if path.exists():
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
            process = psutil.Process(info["pid"])
            alive = abs(process.create_time() - info["created"]) < 0.01
        except psutil.NoSuchProcess:
            alive = False
        except (ValueError, KeyError, OSError, psutil.AccessDenied):
            raise RepairError("修复锁文件无法确认，请检查是否有另一个修复工具正在运行。") from None
        if alive:
            raise RepairError("另一个修复操作正在运行，请等待它完成。")
        path.unlink()
    try:
        with path.open("x", encoding="utf-8") as output:
            json.dump({"pid": os.getpid(), "created": psutil.Process().create_time()}, output)
    except FileExistsError:
        raise RepairError("另一个修复操作正在运行。") from None
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def read_toml(path: Path):
    try:
        raw = path.read_bytes()
        return raw, tomlkit.parse(raw.decode("utf-8-sig"))
    except OSError:
        raise RepairError(f"无法读取配置：{path.name}") from None
    except (ValueError, UnicodeError, tomlkit.exceptions.ParseError) as error:
        line = getattr(error, "line", None)
        where = f"（第 {line} 行）" if line else ""
        raise RepairError(f"{path.name} 的 TOML 格式无效{where}。请先修正语法；原文件未修改。") from None


def redact_toml(text: str) -> str:
    # Rebuild only the display document: comments and original formatting can
    # contain credentials even when their associated setting is not sensitive.
    # The source text and all data used for writing remain untouched.
    try:
        hidden = "•••• 已隐藏 ••••"

        def sanitize(value, trail=()):
            if isinstance(value, dict):
                sensitive = bool(trail and (trail[-1] in {"env", "http_headers", "auth"}
                                            or trail[-2:] == ("shell_environment_policy", "set")))
                safe = {}
                for key, child in value.items():
                    if sensitive or SECRET.search(str(key)):
                        safe[key] = hidden
                    elif key == "args" and trail[:1] == ("mcp_servers",):
                        # Arguments may pass a secret via positional arguments,
                        # flags, nested scripts, or inline assignments.
                        safe[key] = [hidden]
                    else:
                        safe[key] = sanitize(child, (*trail, str(key)))
                return safe
            if isinstance(value, list):
                return [sanitize(child, trail) for child in value]
            if isinstance(value, str):
                # A URL may be embedded in a command or another string. Hide
                # the entire value when credentials/query/fragment delimiters
                # occur after it, including malformed values with whitespace.
                if re.search(r"https?://[\s\S]*[@?#]", value, re.I):
                    return hidden
                if SECRET.search(value) and re.search(r"(?:--?|\b)(?:api[-_]?key|token|secret|password|authorization|bearer)\b", value, re.I):
                    return hidden
            return value

        document = tomlkit.document()
        document.update(sanitize(tomlkit.parse(text).unwrap()))
        text = tomlkit.dumps(document)
    except Exception:
        return "配置无法安全脱敏，未展示原文。"
    return text.rstrip("\r\n")


@dataclass(frozen=True)
class ConnectionSettings:
    mode: str = "keep"
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""


def normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                 and parsed.port != 0 and parsed.username is None and parsed.password is None
                 and not parsed.query and not parsed.fragment and not re.search(r"[\s\\\x00-\x1f\x7f]", value))
    except ValueError:
        valid = False
    if not valid:
        raise RepairError("请输入完整的 http:// 或 https:// 接口地址，不要包含账号、密钥、查询参数或空格。")
    if parsed.path.rstrip("/").endswith(("/responses", "/chat/completions")):
        raise RepairError("请填写 API 基础地址（例如 https://gateway.example.com/v1），不要填写 /responses 或 /chat/completions 请求地址。")
    return value


def connection_summary(home: Path) -> dict:
    """Return display fields only; never expose a saved credential to the UI."""
    _, config = read_toml(home / "config.toml")
    provider = config.get("model_provider", "openai")
    saved = config.get("model_providers", {}).get(RELAY_PROVIDER, {})
    if not isinstance(saved, dict):
        saved = {}
    return {"provider": str(provider), "model": str(config.get("model", "默认模型")),
            "base_url": str(saved.get("base_url", "")),
            "has_key": bool(saved.get("experimental_bearer_token")),
            "has_overrides": bool(config.get("openai_base_url") or config.get("chatgpt_base_url")
                                  or config.get("model_providers", {}).get("openai"))}


def apply_connection(merged, settings: ConnectionSettings, notes: list[str]) -> None:
    if settings.mode not in {"keep", "direct", "relay"}:
        raise RepairError("连接方式无效，请重新选择。")
    if settings.mode == "keep":
        return
    if settings.model.strip():
        model = settings.model.strip()
        if re.search(r"\s|[\x00-\x1f\x7f]", model):
            raise RepairError("模型名称不能包含空格或换行，请填写服务商提供的模型标识。")
        merged["model"] = model
    if settings.mode == "direct":
        merged["model_provider"] = "openai"
        for key in ("openai_base_url", "chatgpt_base_url"):
            if key in merged:
                del merged[key]
                notes.append(f"移除 {key} 地址覆盖，恢复官方默认地址。")
        # Older configs sometimes redefined the now-reserved built-in provider.
        providers = merged.get("model_providers", {})
        if "openai" in providers:
            del providers["openai"]
            notes.append("移除旧版 openai 提供方覆盖，使用内置官方连接。")
        notes.append("切换为 OpenAI 直连，沿用已有官方认证；不读取或修改 auth.json。")
    else:
        base_url = normalize_base_url(settings.base_url)
        saved = merged.get("model_providers", {}).get(RELAY_PROVIDER, {})
        if not isinstance(saved, dict):
            raise RepairError("中转配置格式异常，请先检查本工具专用的提供方配置。")
        key = settings.api_key.strip()
        if not key and saved.get("base_url") == base_url:
            key = saved.get("experimental_bearer_token", "")
        if not isinstance(key, str) or not key:
            raise RepairError("请填写第三方密钥。首次使用或更换接口地址时，必须输入该地址对应的密钥。")
        if re.search(r"[\s\x00-\x1f\x7f]", key) or not key.isascii():
            raise RepairError("密钥包含空格、换行或非英文字符，请检查后重新输入。")
        if "model_providers" not in merged:
            merged["model_providers"] = tomlkit.table()
        # A fresh provider table prevents stale auth/header overrides from winning.
        relay = tomlkit.table()
        relay.update({"name": "Codex Repair Relay", "base_url": base_url,
                      "wire_api": "responses", "requires_openai_auth": False,
                      "experimental_bearer_token": key, "supports_websockets": False})
        merged["model_providers"][RELAY_PROVIDER] = relay
        merged["model_provider"] = RELAY_PROVIDER
        notes.append("切换为第三方中转，使用单独的接口地址和密钥；原有官方登录保留。")
        notes.append("中转需支持 Responses API。密钥以明文保存在本机 config.toml；报告脱敏，原始备份仍可能含密钥。")
    notes.append("启动参数、配置 profile 或外部环境变量若覆盖连接设置，需在 Codex 启动环境中处理。")


@dataclass(frozen=True)
class Options:
    merge_preferences: bool = True
    merge_mcp: bool = False
    repair_history: bool = True
    connection: ConnectionSettings = field(default_factory=ConnectionSettings)
    edited_content: str | None = field(default=None, repr=False)
    content_original_hash: str | None = None


@dataclass
class Change:
    relative: str
    before_hash: str
    after_hash: str
    kind: str
    new_data: bytes = field(repr=False)
    old_header_length: int = 0
    delta: int = 0
    physical_id: str = ""
    body_hash: str = ""


@dataclass
class Plan:
    home: Path
    source: Path | None
    options: Options
    provider: str
    model: str
    changes: list[Change]
    rows: list[dict]
    state_db: str | None
    history_db: str | None
    notes: list[str]
    projects_added: int
    mcp_added: int
    config_diff: str
    source_hash: str | None
    config_hash: str
    provider_counts: dict[str, int]

    @property
    def needed(self) -> bool:
        return bool(self.changes or self.rows)

    @property
    def signature(self) -> str:
        payload = {"config": self.config_hash, "source": self.source_hash,
                   "changes": [(c.relative, c.before_hash, c.after_hash) for c in self.changes],
                   "rows": self.rows, "state": self.state_db, "history": self.history_db}
        return sha(json.dumps(payload, sort_keys=True).encode())

    def report(self) -> dict:
        return {"tool": FORMAT, "operation": "content_repair" if self.options.edited_content is not None else "repair",
                "provider": self.provider, "model": self.model,
                "projects_added": self.projects_added, "mcp_added": self.mcp_added,
                "legacy_threads": len(self.rows), "legacy_providers": self.provider_counts,
                "rollout_files": sum(c.kind == "rollout" for c in self.changes),
                "config_changed": any(c.kind == "config" for c in self.changes),
                "notes": self.notes, "config_diff_redacted": self.config_diff}


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def readonly_db(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10, factory=ClosingConnection)


def columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}


def find_database(home: Path, stem: str) -> str | None:
    paths = []
    for path in home.glob(stem + "_*.sqlite"):
        match = re.fullmatch(re.escape(stem) + r"_(\d+)\.sqlite", path.name)
        if match:
            safe_path(home, path.name)
            paths.append((int(match[1]), path.name))
    return max(paths)[1] if paths else None


def merge_missing(destination, source, keys=None):
    for key in keys if keys is not None else source:
        if key not in source:
            continue
        if key not in destination:
            destination[key] = copy.deepcopy(source[key])
        elif isinstance(destination[key], dict) and isinstance(source[key], dict):
            merge_missing(destination[key], source[key])


def make_config(current_raw: bytes, current, old, options: Options):
    merged = copy.deepcopy(current)
    notes = []
    projects_added = mcp_added = 0
    provider = current.get("model_provider", "openai")
    if not isinstance(provider, str) or not provider.strip():
        raise RepairError("当前 model_provider 配置无效。")
    if "profile" in current or current.get("config_profile"):
        raise RepairError("检测到启用的配置 profile。请先将实际使用的 profile 合并到主配置后再处理，避免选错连接。")
    if "model_provider" not in merged:
        merged["model_provider"] = provider
    apply_connection(merged, options.connection, notes)
    provider = merged["model_provider"]
    if old is not None and options.merge_preferences:
        if "personality" not in merged and old.get("personality") in {"friendly", "pragmatic", "none"}:
            merged["personality"] = old["personality"]
        for name, settings in old.get("projects", {}).items():
            if name not in merged.get("projects", {}) and settings.get("trust_level") == "trusted":
                if "projects" not in merged:
                    merged["projects"] = tomlkit.table()
                merged["projects"][name] = copy.deepcopy(settings)
                projects_added += 1
        if "desktop" in old:
            if "desktop" not in merged:
                merged["desktop"] = tomlkit.table()
            merge_missing(merged["desktop"], old["desktop"], ["localeOverride", "conversationDetailMode", "followUpQueueMode", "open-in-target-preferences"])
        notes.append("合并旧项目和界面偏好；已有设置优先。")
    if old is not None and options.merge_mcp:
        for name, server in old.get("mcp_servers", {}).items():
            if name in RUNTIME_SERVERS or name in merged.get("mcp_servers", {}):
                continue
            command = server.get("command", "")
            if command and (Path(command).is_absolute() or re.match(r"^[A-Za-z]:", command)) and not Path(command).exists():
                notes.append(f"跳过 MCP「{name}」：程序路径在本机不存在。")
                continue
            if "mcp_servers" not in merged:
                merged["mcp_servers"] = tomlkit.table()
            merged["mcp_servers"][name] = copy.deepcopy(server)
            mcp_added += 1
    if options.connection.mode == "keep":
        notes.append("保留当前模型、连接、登录配置、插件和桌面运行路径。")
    else:
        notes.append("保留插件、桌面运行路径和其余配置。模型名称留空时保留当前模型。")
    notes.append("不导入旧网关、旧运行环境校验值和已过时的 disable_response_storage。")
    result = tomlkit.dumps(merged)
    if b"\r\n" in current_raw:
        result = result.replace("\r\n", "\n").replace("\n", "\r\n")
    payload = (b"\xef\xbb\xbf" if current_raw.startswith(b"\xef\xbb\xbf") else b"") + result.encode("utf-8")
    tomlkit.parse(payload.decode("utf-8-sig"))
    return payload, provider, notes, projects_added, mcp_added


def discover_rollouts(home: Path):
    result = []
    seen = set()
    for folder in ["sessions", "archived_sessions"]:
        root = home / folder
        if not root.exists():
            continue
        safe_path(home, folder)
        for path in sorted(root.rglob("*.jsonl")):
            relative = path.relative_to(home).as_posix()
            safe_path(home, relative)
            with path.open("rb") as stream:
                header = stream.readline(MAX_HEADER + 1)
            if not header:
                continue
            if len(header) > MAX_HEADER or not header.endswith(b"\n"):
                raise RepairError(f"历史文件首行不完整或超过支持范围：{path.name}")
            try:
                record = json.loads(header)
            except (ValueError, UnicodeError):
                raise RepairError(f"历史文件的元数据无法解析：{path.name}") from None
            if record.get("type") != "session_meta" or not isinstance(record.get("payload"), dict):
                raise RepairError(f"暂不支持此历史文件格式：{path.name}")
            match = UUID_END.search(path.name)
            physical_id = match.group(1).lower() if match else record["payload"].get("session_id", record["payload"].get("id"))
            if not physical_id or physical_id in seen:
                raise RepairError("历史文件存在重复或缺失的物理会话 ID，已停止自动迁移。")
            seen.add(physical_id)
            result.append({"path": path, "relative": relative, "header": header, "record": record, "physical_id": physical_id})
    return result


def plan_rollout_changes(records: list[dict], provider: str) -> list[Change]:
    by_id = {r["physical_id"]: r for r in records}
    deltas = {key: 0 for key in by_id}
    headers = {}
    for _ in range(32):
        new_deltas = {}
        for item in records:
            record = copy.deepcopy(item["record"])
            payload = record["payload"]
            source_provider = payload.get("model_provider")
            changed = isinstance(source_provider, str) and source_provider != provider
            if changed:
                payload["model_provider"] = provider
            base = payload.get("history_base")
            if isinstance(base, dict) and base.get("thread_id") in by_id:
                delta = deltas[base["thread_id"]]
                offset = base.get("end_byte_offset")
                if delta and isinstance(offset, int) and offset > 0:
                    if offset < len(by_id[base["thread_id"]]["header"]):
                        raise RepairError("历史分支的字节索引位于元数据内部，无法安全迁移。")
                    base["end_byte_offset"] = offset + delta
                    changed = True
            if changed:
                ending = b"\r\n" if item["header"].endswith(b"\r\n") else b"\n"
                bom = b"\xef\xbb\xbf" if item["header"].startswith(b"\xef\xbb\xbf") else b""
                header = bom + json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + ending
            else:
                header = item["header"]
            headers[item["physical_id"]] = header
            new_deltas[item["physical_id"]] = len(header) - len(item["header"])
        if deltas == new_deltas:
            break
        deltas = new_deltas
    else:
        raise RepairError("历史分支索引未能收敛，已停止自动迁移。")
    changes = []
    for item in records:
        header = headers[item["physical_id"]]
        if header == item["header"]:
            continue
        digest = hashlib.sha256(header)
        body_digest = hashlib.sha256()
        with item["path"].open("rb") as source:
            source.readline()
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                body_digest.update(chunk)
        changes.append(Change(item["relative"], file_hash(item["path"]), digest.hexdigest(), "rollout", header,
                              len(item["header"]), deltas[item["physical_id"]], item["physical_id"], body_digest.hexdigest()))
    return changes


def validate_history_schema(path: Path):
    with readonly_db(path) as db:
        required = {"thread_turns": {"thread_id", "turn_id", "rollout_byte_offset", "rollout_end_byte_offset"},
                    "thread_history_projection_state": {"thread_id", "next_rollout_byte_offset"}}
        for table, keys in required.items():
            if not keys.issubset(columns(db, table)):
                raise RepairError("此版本的分页历史数据库结构暂不支持；可以取消历史修复，仅合并配置。")
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            offset_columns = {c for c in columns(db, table) if "byte_offset" in c}
            if offset_columns - required.get(table, set()):
                raise RepairError("发现未知的历史字节索引字段，已停止迁移以保护历史记录。")


def analyze(home: Path, source: Path | None = None, options: Options = Options(), progress=lambda text: None) -> Plan:
    home = home.expanduser().resolve()
    if not home.is_dir():
        raise RepairError("所选 Codex 目录不存在。")
    if options.edited_content is not None:
        return _analyze_content(home, options, progress)
    progress("读取当前配置与旧配置…")
    current_raw, current = read_toml(safe_path(home, "config.toml"))
    old = old_raw = None
    if source:
        source = source.expanduser().resolve()
        if source == home / "config.toml":
            raise RepairError("旧配置不能与当前 config.toml 是同一个文件。")
        old_raw, old = read_toml(source)
    merged, provider, notes, projects, mcp = make_config(current_raw, current, old, options)
    changes = []
    if merged != current_raw:
        changes.append(Change("config.toml", sha(current_raw), sha(merged), "config", merged))
    diff = "\n".join(difflib.unified_diff(redact_toml(current_raw.decode("utf-8-sig")).splitlines(),
                                           redact_toml(merged.decode("utf-8-sig")).splitlines(),
                                           fromfile="当前配置", tofile="修复后配置", lineterm=""))
    state_name = history_name = None
    rows = []
    counts = {}
    if options.repair_history:
        sqlite_home = current.get("sqlite_home")
        if sqlite_home and Path(sqlite_home).expanduser().resolve() != home:
            raise RepairError("检测到独立的 sqlite_home，当前版本仅支持数据库与配置位于同一目录。可取消历史修复后继续。")
        progress("扫描会话元数据和分页索引…")
        state_name = find_database(home, "state")
        history_name = find_database(home, "thread_history")
        records = discover_rollouts(home)
        file_changes = plan_rollout_changes(records, provider)
        if state_name:
            with readonly_db(home / state_name) as db:
                needed = {"id", "model_provider", "rollout_path"}
                if not needed.issubset(columns(db, "threads")):
                    raise RepairError("会话索引结构暂不支持，已停止历史修复。")
                for thread_id, old_provider, rollout_path in db.execute("SELECT id,model_provider,rollout_path FROM threads WHERE model_provider != ? ORDER BY id", (provider,)):
                    candidate = Path(rollout_path)
                    if not candidate.is_absolute():
                        candidate = home / candidate
                    if not candidate.exists() or not candidate.resolve().is_relative_to(home):
                        raise RepairError("部分旧会话的历史文件缺失或位于所选目录之外，已停止历史迁移。")
                    rows.append({"id": thread_id, "before": old_provider, "after": provider})
                    counts[old_provider] = counts.get(old_provider, 0) + 1
        changed_ids = {c.physical_id for c in file_changes}
        needs_paginated = any(r["physical_id"] in changed_ids and r["record"]["payload"].get("history_mode") == "paginated" for r in records)
        if needs_paginated and not history_name:
            raise RepairError("历史文件使用分页存储，但分页数据库缺失。已停止修复。")
        if history_name and file_changes:
            validate_history_schema(home / history_name)
        changes.extend(file_changes)
        notes.append(f"将旧提供方的会话标记统一为 {provider}，同时维护历史分页和分支索引。")
    if not source:
        notes.append("未选择旧配置：只处理当前配置与历史兼容性。")
    model = str(tomlkit.parse(merged.decode("utf-8-sig")).get("model", "默认模型"))
    return Plan(home, source, options, provider, model, changes, rows,
                state_name, history_name, notes, projects, mcp, diff, sha(old_raw) if old_raw is not None else None,
                sha(current_raw), counts)


def read_config_content(path: Path) -> tuple[bytes, str]:
    """Read an editable local file without requiring its original TOML to parse."""
    try:
        if path.stat().st_size > MAX_CONFIG:
            raise RepairError("配置文件超过 2 MB，无法在内容编辑器中处理。")
        raw = path.read_bytes()
        if len(raw) > MAX_CONFIG:
            raise RepairError("配置文件超过 2 MB，无法在内容编辑器中处理。")
        encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        return raw, raw.decode(encoding)
    except OSError:
        raise RepairError("无法读取配置文件，请检查所选路径和访问权限。") from None
    except UnicodeError:
        raise RepairError("配置文件编码无法识别，请先转换为 UTF-8 后重新导入。") from None


def analyze_content(home: Path, text: str, original_hash: str | None = None, progress=lambda text: None) -> Plan:
    options = Options(False, False, False, edited_content=text, content_original_hash=original_hash)
    return analyze(home, options=options, progress=progress)


def _analyze_content(home: Path, options: Options, progress) -> Plan:
    from .content import check_content

    progress("检查配置内容和当前文件版本…")
    current_raw, original = read_config_content(safe_path(home, "config.toml"))
    if options.content_original_hash and sha(current_raw) != options.content_original_hash:
        raise RepairError("当前配置在读取后发生变化，请重新读取文件，再编辑和预览。")
    text = options.edited_content
    if len(text.encode("utf-8")) > MAX_CONFIG:
        raise RepairError("配置内容超过 2 MB，请缩小文件后再处理。")
    checked = check_content(text)
    if not checked.valid:
        errors = [issue.message for issue in checked.issues if issue.severity == "error"]
        raise RepairError("配置尚有错误，无法保存：" + "；".join(errors[:3]))
    normalized = checked.text.replace("\r\n", "\n").replace("\r", "\n")
    if b"\r\n" in current_raw or "\r\n" in original:
        normalized = normalized.replace("\n", "\r\n")
    payload = (b"\xef\xbb\xbf" if current_raw.startswith(b"\xef\xbb\xbf") else b"") + normalized.encode("utf-8")
    document = tomlkit.parse(normalized)
    changes = [Change("config.toml", sha(current_raw), sha(payload), "config", payload)] if current_raw != payload else []
    diff = "\n".join(difflib.unified_diff(redact_toml(original).splitlines(),
                                       redact_toml(normalized).splitlines(),
                                       fromfile="当前配置", tofile="内容修复后配置", lineterm=""))
    notes = ["本次仅保存经过检查的 config.toml；写入前自动备份原始文件。",
             "内容编辑区保留密钥原文；检测报告和差异预览自动隐藏敏感值。",
             "检查覆盖 TOML 语法和已支持的常见配置项；接口连通性与所有版本兼容性需在 Codex 中验证。"]
    notes.extend(issue.message for issue in checked.issues if issue.severity != "error")
    try:
        old_doc = tomlkit.parse(original)
    except (ValueError, tomlkit.exceptions.ParseError):
        notes.append("原文件无法解析，预览隐藏原始正文；备份仍会保存完整原件，可按需恢复。")
    else:
        if old_doc.get("model_provider", "openai") != document.get("model_provider", "openai"):
            notes.append("本次编辑更改了提供方。保存后可到「检查与修复」同步历史记录标记。")
    if current_raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        notes.append("将 UTF-16 编码转换为 Codex 使用的 UTF-8，原始编码文件会完整备份。")
    return Plan(home, None, options, str(document.get("model_provider", "openai")),
                str(document.get("model", "默认模型")), changes, [], None, None,
                notes, 0, 0, diff, None, sha(current_raw), {})


def atomic_json(path: Path, value: dict):
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def snapshot_database(source: Path, destination: Path):
    with readonly_db(source) as live, sqlite3.connect(destination, factory=ClosingConnection) as backup:
        live.backup(backup)
        if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RepairError("数据库完整性检查失败，未进行修复。")


def build_db_patches(plan: Plan) -> list[dict]:
    patches = []
    if plan.state_db:
        for row in plan.rows:
            patches.append({"db": plan.state_db, "table": "threads", "key": {"id": row["id"]},
                            "before": {"model_provider": row["before"]}, "after": {"model_provider": row["after"]}})
    if plan.history_db:
        with readonly_db(plan.home / plan.history_db) as db:
            for change in plan.changes:
                if change.kind != "rollout" or not change.delta:
                    continue
                for table, key_fields, offset_fields in [
                    ("thread_turns", ["thread_id", "turn_id"], ["rollout_byte_offset", "rollout_end_byte_offset"]),
                    ("thread_history_projection_state", ["thread_id"], ["next_rollout_byte_offset"]),
                ]:
                    fields = key_fields + offset_fields
                    for values in db.execute(f"SELECT {','.join(fields)} FROM {table} WHERE thread_id=?", (change.physical_id,)):
                        row = dict(zip(fields, values))
                        before = {k: row[k] for k in offset_fields}
                        after = before.copy()
                        for key, value in before.items():
                            if value is not None and value > 0:
                                if value < change.old_header_length:
                                    raise RepairError("分页索引位于会话元数据内部，无法安全移动。")
                                after[key] = value + change.delta
                        if before != after:
                            patches.append({"db": plan.history_db, "table": table, "key": {k: row[k] for k in key_fields}, "before": before, "after": after})
    return patches


ALLOWED_PATCHES = {
    "threads": ({"id"}, {"model_provider"}),
    "thread_turns": ({"thread_id", "turn_id"}, {"rollout_byte_offset", "rollout_end_byte_offset"}),
    "thread_history_projection_state": ({"thread_id"}, {"next_rollout_byte_offset"}),
}


def validate_patch(patch: dict):
    allowed = ALLOWED_PATCHES.get(patch.get("table"))
    if not allowed or set(patch.get("key", {})) != allowed[0] or set(patch.get("before", {})) != allowed[1] or set(patch.get("after", {})) != allowed[1]:
        raise RepairError("备份中的数据库补丁格式无效。")
    stem = "state" if patch["table"] == "threads" else "thread_history"
    if not re.fullmatch(stem + r"_\d+\.sqlite", patch.get("db", "")):
        raise RepairError("备份中的数据库路径无效。")


def patch_value(db: sqlite3.Connection, patch: dict) -> dict:
    fields = list(patch["before"])
    where = " AND ".join(f'"{key}" IS ?' for key in patch["key"])
    row = db.execute(f'SELECT {",".join(fields)} FROM "{patch["table"]}" WHERE {where}', tuple(patch["key"].values())).fetchone()
    if row is None:
        raise RepairError("会话索引已发生变化，请重新扫描。")
    return dict(zip(fields, row))


def apply_patches(home: Path, patches: list[dict], reverse=False, allow_already=False):
    connections = {}
    committed = []
    source_key, destination_key = ("after", "before") if reverse else ("before", "after")
    try:
        for patch in patches:
            validate_patch(patch)
            if patch["db"] not in connections:
                db = sqlite3.connect(safe_path(home, patch["db"]), timeout=10)
                db.execute("BEGIN IMMEDIATE")
                connections[patch["db"]] = db
            db = connections[patch["db"]]
            existing = patch_value(db, patch)
            if allow_already and existing == patch[destination_key]:
                continue
            if existing != patch[source_key]:
                raise RepairError("数据库已发生新的变化，未覆盖新内容。请重新扫描或手动检查。")
            values = patch[destination_key]
            assignments = ",".join(f'"{key}"=?' for key in values)
            where = " AND ".join(f'"{key}" IS ?' for key in patch["key"])
            db.execute(f'UPDATE "{patch["table"]}" SET {assignments} WHERE {where}', (*values.values(), *patch["key"].values()))
        for db in connections.values():
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RepairError("数据库完整性检查失败。")
        for name, db in connections.items():
            db.commit()
            committed.append(name)
    except Exception:
        for name, db in connections.items():
            if name not in committed:
                db.rollback()
        # A manifest remains available for recovery even if one database committed.
        raise
    finally:
        for db in connections.values():
            db.close()


def list_backups(home: Path) -> list[dict]:
    result = []
    folder = home / BACKUP_DIR
    if not folder.exists():
        return result
    safe_path(home, BACKUP_DIR)
    for path in sorted(folder.glob("*/manifest.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("format") == FORMAT:
                result.append({"path": path.parent, "created_at": data.get("created_at", ""),
                               "state": data.get("state", "unknown"), "files": len(data.get("files", [])),
                               "threads": data.get("summary", {}).get("legacy_threads", 0)})
        except (ValueError, OSError):
            continue
    return sorted(result, key=lambda item: item["created_at"], reverse=True)


def execute(plan: Plan, progress=lambda text: None, process_probe=running_codex, fault_hook=None) -> Path:
    require_closed(process_probe)
    with operation_lock(plan.home):
        if any(b["state"] in {"prepared", "applying", "recovery_required", "restoring"} for b in list_backups(plan.home)):
            raise RepairError("存在未完成的操作，请先到「备份与恢复」处理该备份。")
        fresh = analyze(plan.home, plan.source, plan.options, progress)
        if fresh.signature != plan.signature:
            raise RepairError("文件在预览后发生变化，请重新扫描再执行。")
        if not plan.needed:
            raise RepairError("没有需要修改的内容。")
        patches = build_db_patches(plan)
        backup_root = plan.home / BACKUP_DIR
        backup_root.mkdir(exist_ok=True)
        safe_path(plan.home, BACKUP_DIR)
        backup = backup_root / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + uuid4().hex[:8])
        backup.mkdir()
        manifest = {"format": FORMAT, "home": str(plan.home), "created_at": datetime.now().isoformat(timespec="microseconds"),
                    "state": "prepared", "files": [], "patches": patches, "databases": [], "summary": plan.report()}
        progress("创建完整备份…")
        try:
            total_bytes = sum((plan.home / c.relative).stat().st_size * 3 for c in plan.changes)
            total_bytes += sum((plan.home / name).stat().st_size for name in {p["db"] for p in patches})
            if shutil.disk_usage(plan.home).free < total_bytes + 32 * 1024 * 1024:
                raise RepairError("可用磁盘空间不足，无法同时保存备份和临时文件。")
            for name in sorted({p["db"] for p in patches}):
                destination = backup / "databases" / name
                destination.parent.mkdir(exist_ok=True)
                snapshot_database(plan.home / name, destination)
                manifest["databases"].append({"name": name, "sha256": file_hash(destination)})
            for index, change in enumerate(plan.changes, 1):
                progress(f"备份与校验文件 {index}/{len(plan.changes)}…")
                path = safe_path(plan.home, change.relative)
                before = safe_path(backup / "originals", change.relative)
                stage = safe_path(backup / "staged", change.relative)
                before.parent.mkdir(parents=True, exist_ok=True)
                stage.parent.mkdir(parents=True, exist_ok=True)
                stat = path.stat()
                shutil.copy2(path, before)
                if file_hash(before) != change.before_hash:
                    raise RepairError("源文件在备份时发生变化，请重新扫描。")
                with stage.open("xb") as output:
                    output.write(change.new_data)
                    if change.kind == "rollout":
                        with before.open("rb") as source:
                            source.readline()
                            shutil.copyfileobj(source, output, 1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                if file_hash(stage) != change.after_hash or (change.body_hash and file_hash(stage, body=True) != change.body_hash):
                    raise RepairError("文件校验失败，原始数据未修改。")
                manifest["files"].append({"relative": change.relative, "kind": change.kind, "before_hash": change.before_hash,
                                          "after_hash": change.after_hash, "body_hash": change.body_hash,
                                          "atime_ns": stat.st_atime_ns, "mtime_ns": stat.st_mtime_ns})
            atomic_json(backup / "manifest.json", manifest)
            require_closed(process_probe)
            for item in manifest["files"]:
                if file_hash(safe_path(plan.home, item["relative"])) != item["before_hash"]:
                    raise RepairError("源文件已发生变化，已保留备份并停止操作。")
            manifest["state"] = "applying"
            atomic_json(backup / "manifest.json", manifest)
            progress("保存修复后的配置内容…" if plan.options.edited_content is not None else "更新配置和历史标记…")
            for index, item in enumerate(manifest["files"]):
                path = safe_path(plan.home, item["relative"])
                os.replace(safe_path(backup / "staged", item["relative"]), path)
                os.utime(path, ns=(item["atime_ns"], item["mtime_ns"]))
                if fault_hook:
                    fault_hook(index)
            progress("更新并验证分页索引…")
            apply_patches(plan.home, patches)
            if any(change.kind == "config" for change in plan.changes):
                read_toml(safe_path(plan.home, "config.toml"))
            for item in manifest["files"]:
                path = safe_path(plan.home, item["relative"])
                if file_hash(path) != item["after_hash"] or (item["body_hash"] and file_hash(path, True) != item["body_hash"]):
                    raise RepairError("写入后的文件校验失败。")
            manifest["state"] = "completed"
            manifest["verification"] = {"toml": "passed", "transcript_bodies": "unchanged", "sqlite_integrity": "passed", "inference": "not_tested"}
            atomic_json(backup / "manifest.json", manifest)
            atomic_json(backup / "report.json", {**plan.report(), "verification": manifest["verification"]})
            progress("修复完成，备份和校验报告已保存。")
            return backup
        except Exception as error:
            if manifest["state"] == "applying":
                try:
                    _restore(plan.home, backup, manifest, recovery=True)
                    manifest["state"] = "rolled_back"
                except Exception:
                    manifest["state"] = "recovery_required"
            else:
                manifest["state"] = "cancelled"
            atomic_json(backup / "manifest.json", manifest)
            if isinstance(error, RepairError):
                raise
            raise RepairError(f"操作未完成，备份状态：{manifest['state']}。请在备份页查看；没有输出配置中的凭据。") from None


def _restore(home: Path, backup: Path, manifest: dict, recovery=False, process_probe=None):
    # Validate everything before the first mutation; never restore a whole live database.
    for item in manifest["files"]:
        relative = item.get("relative", "")
        valid_config = item.get("kind") == "config" and relative == "config.toml"
        valid_rollout = item.get("kind") == "rollout" and relative.startswith(("sessions/", "archived_sessions/")) and relative.endswith(".jsonl")
        if not (valid_config or valid_rollout):
            raise RepairError("备份包含本工具处理范围之外的文件，已停止恢复。")
        path = safe_path(home, item["relative"])
        original = safe_path(backup / "originals", item["relative"])
        if not original.is_file() or file_hash(original) != item["before_hash"]:
            raise RepairError("原始备份文件缺失或校验不一致，已停止恢复。")
        accepted = {item["after_hash"], item["before_hash"]} if recovery else {item["after_hash"]}
        if not path.is_file() or file_hash(path) not in accepted:
            raise RepairError("修复后有文件产生了新内容，自动恢复会覆盖新记录，已停止。请保留备份后手动合并。")
    connections = {}
    try:
        for patch in manifest["patches"]:
            validate_patch(patch)
            if patch["db"] not in connections:
                connections[patch["db"]] = readonly_db(safe_path(home, patch["db"]))
            current = patch_value(connections[patch["db"]], patch)
            accepted = [patch["after"], patch["before"]] if recovery else [patch["after"]]
            if current not in accepted:
                raise RepairError("历史索引在修复后发生了变化，已停止自动恢复。")
    finally:
        for db in connections.values():
            db.close()
    if process_probe is not None:
        require_closed(process_probe)
    manifest["state"] = "restoring"
    atomic_json(backup / "manifest.json", manifest)
    apply_patches(home, manifest["patches"], reverse=True, allow_already=recovery)
    for item in manifest["files"]:
        path = safe_path(home, item["relative"])
        temp = path.with_name(path.name + ".repair-restore-" + uuid4().hex)
        shutil.copy2(safe_path(backup / "originals", item["relative"]), temp)
        os.replace(temp, path)
        os.utime(path, ns=(item["atime_ns"], item["mtime_ns"]))
        if file_hash(path) != item["before_hash"]:
            raise RepairError("恢复后的文件校验失败，请保留当前备份。")
    manifest["state"] = "restored"
    atomic_json(backup / "manifest.json", manifest)


def restore(home: Path, backup: Path, progress=lambda text: None, process_probe=running_codex) -> Path:
    home, backup = home.resolve(), backup.resolve()
    require_closed(process_probe)
    try:
        manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise RepairError("无法读取备份清单。请选择本工具生成的备份。") from None
    if manifest.get("format") != FORMAT or Path(manifest.get("home", "")).resolve() != home:
        raise RepairError("该备份不属于当前所选 Codex 目录。")
    if manifest.get("state") not in {"completed", "prepared", "applying", "recovery_required", "restoring"}:
        raise RepairError("该备份不需要恢复，或已经恢复过。")
    with operation_lock(home):
        progress("核对备份与当前数据，检查是否有新增内容…")
        recovery = manifest["state"] != "completed"
        _restore(home, backup, manifest, recovery, process_probe)
        progress("已恢复；未替换整库，也未移除其他会话。")
    return backup
