"""Local, conservative checks for editable Codex TOML content.

This module never reads files, contacts a service, or includes input values in
diagnostics. Unknown settings are preserved for compatibility with newer Codex
versions. Only unambiguous formatting and explicitly known boolean types have
automatic repairs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import tomlkit
from tomlkit.exceptions import ParseError, TOMLKitError


@dataclass(frozen=True)
class ContentIssue:
    severity: str
    message: str
    line: int | None = None
    fixable: bool = False


@dataclass
class ContentCheck:
    text: str = field(repr=False)
    issues: list[ContentIssue] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


BUILTIN_PROVIDERS = {"openai", "ollama", "lmstudio", "amazon-bedrock"}
ROOT_STRINGS = {"model", "model_provider", "model_reasoning_effort"}
ROOT_ENUMS = {
    "model_verbosity": {"low", "medium", "high"},
    "model_reasoning_summary": {"auto", "concise", "detailed", "none"},
    "personality": {"none", "friendly", "pragmatic"},
}
ROOT_BOOLS = {"model_supports_reasoning_summaries"}
PROVIDER_BOOLS = {"requires_openai_auth", "supports_websockets"}
SECTION_TABLES = {"model_providers", "mcp_servers", "projects", "desktop"}
FENCED_DOCUMENT = re.compile(
    r"\A[ \t\r\n]*```(?:toml)?[ \t]*\r?\n(?P<body>[\s\S]*?)"
    r"^[ \t]*```[ \t]*(?:\r?\n)?[ \t\r\n]*\Z", re.IGNORECASE | re.MULTILINE,
)
SMART_STRING = re.compile(
    r"(?m)^(?P<prefix>[ \t]*[A-Za-z0-9_-]+[ \t]*=[ \t]*)"
    r"“(?P<body>[^”\r\n]*)”(?P<suffix>[ \t]*(?:#[^\r\n]*)?)(?P<end>\r?$)"
)


def _outside_strings_and_comments(text: str) -> list[bool]:
    """Return lexical visibility, respecting both types of multiline strings."""
    outside = [True] * len(text)
    position = 0
    quote: str | None = None
    while position < len(text):
        if quote is not None:
            if quote in {'"', '"""'} and text[position] == "\\":
                outside[position] = False
                position += 1
                if position < len(text):
                    outside[position] = False
                    position += 1
                continue
            if text.startswith(quote, position):
                for index in range(position, position + len(quote)):
                    outside[index] = False
                position += len(quote)
                quote = None
                continue
            outside[position] = False
            position += 1
            continue
        if text[position] == "#":
            while position < len(text) and text[position] not in "\r\n":
                outside[position] = False
                position += 1
            continue
        if text[position] in {"'", '"'}:
            quote = text[position] * (3 if text.startswith(text[position] * 3, position) else 1)
            for index in range(position, position + len(quote)):
                outside[index] = False
            position += len(quote)
            continue
        position += 1
    return outside


def _repair_smart_delimiters(text: str) -> tuple[str, bool]:
    outside = _outside_strings_and_comments(text)
    changes: list[tuple[int, int, str]] = []
    for match in SMART_STRING.finditer(text):
        opening = match.end("prefix")
        # A syntactically similar line inside a multiline string is content.
        if not outside[opening] or not outside[match.end("prefix") - 1]:
            continue
        changes.append((opening, opening + 1, '"'))
        closing = match.end("body")
        changes.append((closing, closing + 1, '"'))
    for start, end, replacement in reversed(changes):
        text = text[:start] + replacement + text[end:]
    return text, bool(changes)


def _repair_boolean_case(text: str) -> tuple[str, bool]:
    outside = _outside_strings_and_comments(text)
    changes: list[tuple[int, int, str]] = []
    value = False
    header = False
    nesting: list[str] = []
    position = 0
    while position < len(text):
        if not outside[position]:
            position += 1
            continue
        char = text[position]
        if char in "\r\n" and not nesting:
            value = False
            header = False
        elif not value and char == "[":
            header = True
        elif not value and char == "=" and not header:
            value = True
        elif value and char in "[{":
            nesting.append(char)
        elif value and char in "]}" and nesting:
            nesting.pop()
        if value and char in "TF":
            token = "True" if char == "T" else "False"
            end = position + len(token)
            previous = text[position - 1] if position else ""
            following = text[end] if end < len(text) else ""
            if (text.startswith(token, position)
                    and all(outside[position:end])
                    and not (previous.isalnum() or (previous and previous in "_.-"))
                    and not (following.isalnum() or (following and following in "_.-"))):
                after = end
                while after < len(text) and text[after] in " \t":
                    after += 1
                # True and False are also legal bare keys in inline tables.
                if after == len(text) or text[after] != "=":
                    changes.append((position, end, token.lower()))
                    position = end
                    continue
        position += 1
    for start, end, replacement in reversed(changes):
        text = text[:start] + replacement + text[end:]
    return text, bool(changes)


def _parse(text: str):
    try:
        return tomlkit.parse(text), None
    except (ParseError, TOMLKitError, ValueError, RecursionError) as error:
        # Parser exception text can contain user keys or secret-bearing input.
        line = getattr(error, "line", None)
        column = getattr(error, "col", None)
        if not isinstance(line, int) or line < 1:
            line = None
        location = f"（第 {line} 行" if line else ""
        if location and isinstance(column, int) and column >= 0:
            location += f"，第 {column + 1} 列"
        if location:
            location += "）"
        return None, ContentIssue(
            "error", f"TOML 语法有误{location}，请检查引号、重复键和表结构。", line,
        )


def _syntax_candidate(text: str):
    candidate = text
    fixes: list[str] = []
    if candidate.startswith("\ufeff"):
        candidate = candidate[1:]
        fixes.append("已移除内容开头的 UTF-8 BOM 标记。")
    match = FENCED_DOCUMENT.fullmatch(candidate)
    if match:
        candidate = match.group("body")
        fixes.append("已移除包围整个配置的 Markdown 代码块标记。")
    document, issue = _parse(candidate)
    if issue is None:
        return candidate, document, fixes
    candidate, changed = _repair_smart_delimiters(candidate)
    if changed:
        fixes.append("已将单行字符串的弯引号分隔符改为 TOML 双引号。")
    candidate, changed = _repair_boolean_case(candidate)
    if changed:
        fixes.append("已将字符串和注释之外的布尔值改为小写 true / false。")
    document, issue = _parse(candidate)
    if issue is not None:
        # Never leave a partial syntax transformation in an invalid document.
        return text, None, []
    return candidate, document, fixes


def _boolean_paths(values: dict):
    for key in sorted(ROOT_BOOLS):
        if key in values:
            yield (key,), values[key], key
    for section, fields in (("model_providers", PROVIDER_BOOLS), ("mcp_servers", {"enabled"})):
        entries = values.get(section)
        if not isinstance(entries, dict):
            continue
        for name, entry in entries.items():
            if isinstance(entry, dict):
                for key in sorted(fields):
                    if key in entry:
                        yield (section, name, key), entry[key], f"{section} 中的 {key}"


def _root_line(text: str, key: str) -> int | None:
    outside = _outside_strings_and_comments(text)
    offset = 0
    pattern = re.compile(rf"[ \t]*{re.escape(key)}[ \t]*=")
    for line, content in enumerate(text.splitlines(keepends=True), 1):
        stripped = content.lstrip(" \t")
        first = offset + len(content) - len(stripped)
        if first < len(text) and outside[first]:
            if stripped.startswith("["):
                break
            if pattern.match(content):
                return line
        offset += len(content)
    return None


def _valid_base_url(value: str) -> bool:
    if (not value or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
            or any(char in value for char in "\\?#")):
        return False
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            return False
        parsed.port  # Reject malformed port values without displaying them.
    except ValueError:
        return False
    return True


def _semantic_issues(values: dict, text: str) -> list[ContentIssue]:
    issues: list[ContentIssue] = []

    def add(message: str, key: str | None = None, *, severity="error", fixable=False):
        issues.append(ContentIssue(severity, message, _root_line(text, key) if key else None, fixable))

    for key in sorted(ROOT_STRINGS):
        if key in values and (not isinstance(values[key], str) or not values[key].strip()):
            add(f"{key} 必须是非空字符串。", key)
    for key, allowed in ROOT_ENUMS.items():
        if key in values and (not isinstance(values[key], str) or values[key] not in allowed):
            add(f"{key} 的值不受支持，请检查该设置。", key)
    for _, value, description in _boolean_paths(values):
        if not isinstance(value, bool):
            add(f"{description} 必须是布尔值 true 或 false。",
                description if description in ROOT_BOOLS else None,
                fixable=isinstance(value, str) and value in {"true", "false"})
    if "notify" in values and (not isinstance(values["notify"], list)
                              or any(not isinstance(item, str) for item in values["notify"])):
        add("notify 必须是由字符串组成的数组。", "notify")
    for section in sorted(SECTION_TABLES):
        if section in values and not isinstance(values[section], dict):
            add(f"{section} 必须使用 TOML 表结构。", section)

    providers = values.get("model_providers", {})
    active = values.get("model_provider", "openai")
    if isinstance(active, str) and active.strip() and active not in BUILTIN_PROVIDERS:
        if not isinstance(providers, dict) or active not in providers:
            add("当前第三方提供方未在 model_providers 中定义。", "model_provider")
    if isinstance(providers, dict):
        for name, provider in providers.items():
            if not isinstance(provider, dict):
                add("model_providers 中的每个提供方必须是 TOML 表。")
                continue
            if name not in BUILTIN_PROVIDERS and "name" not in provider:
                add("第三方提供方缺少 name 名称设置。")
            for key in ("name", "base_url", "env_key", "experimental_bearer_token"):
                if key in provider and (not isinstance(provider[key], str) or not provider[key].strip()):
                    add(f"提供方的 {key} 必须是非空字符串。")
            if isinstance(provider.get("base_url"), str) and not _valid_base_url(provider["base_url"]):
                add("提供方的 base_url 必须是有效的 HTTP / HTTPS 地址，且不能包含账号、查询参数或片段。")
            if "wire_api" in provider and provider["wire_api"] != "responses":
                add("提供方的 wire_api 当前仅支持 responses，请确认服务兼容后手动修改。")
            if "auth" in provider and not isinstance(provider["auth"], dict):
                add("提供方的 auth 必须是 TOML 表。")
            mechanisms = sum(key in provider for key in ("auth", "env_key", "experimental_bearer_token"))
            if mechanisms > 1:
                add("提供方同时设置了多种密钥来源，请手动保留一种认证方式。")
            if provider.get("requires_openai_auth") is True and mechanisms:
                add("提供方同时启用了 OpenAI 登录和其他密钥来源，请手动选择一种认证方式。")

    servers = values.get("mcp_servers", {})
    if isinstance(servers, dict):
        for server in servers.values():
            if not isinstance(server, dict):
                add("mcp_servers 中的每个服务器必须是 TOML 表。")
                continue
            if sum(key in server for key in ("command", "url")) != 1:
                add("MCP 服务器必须选择 command 或 url 其中一种连接方式。")
            for key in ("command", "url"):
                if key in server and (not isinstance(server[key], str) or not server[key].strip()):
                    add(f"MCP 服务器的 {key} 必须是非空字符串。")
            if "args" in server and (not isinstance(server["args"], list)
                                    or any(not isinstance(item, str) for item in server["args"])):
                add("MCP 服务器的 args 必须是由字符串组成的数组。")
    projects = values.get("projects", {})
    if isinstance(projects, dict) and any(not isinstance(project, dict) for project in projects.values()):
        add("projects 中的每个项目必须是 TOML 表。")
    if "disable_response_storage" in values:
        add("disable_response_storage 是旧配置项，请根据所用 Codex 版本确认是否仍需保留。",
            "disable_response_storage", severity="warning")
    return issues


def check_content(text: str, auto_fix: bool = False) -> ContentCheck:
    """Validate TOML; optionally apply conservative, locally safe repairs."""
    document, syntax_issue = _parse(text)
    candidate, repaired_document, syntax_fixes = _syntax_candidate(text)
    if syntax_issue is not None:
        if not auto_fix or repaired_document is None:
            if repaired_document is not None and syntax_fixes:
                syntax_issue = ContentIssue("error", syntax_issue.message, syntax_issue.line, True)
            return ContentCheck(text, [syntax_issue])
        text = candidate
        document = repaired_document
    else:
        syntax_fixes = []
    values = document.unwrap()
    if not values:
        return ContentCheck(text, [ContentIssue("error", "配置内容为空或只有注释，请填写配置后再保存。")], syntax_fixes)
    fixes = list(syntax_fixes)
    if auto_fix:
        for path, value, description in _boolean_paths(values):
            if isinstance(value, str) and value in {"true", "false"}:
                entry = document
                for part in path[:-1]:
                    entry = entry[part]
                entry[path[-1]] = value == "true"
                fixes.append(f"已将 {description} 的字符串布尔值改为 TOML 布尔值。")
        if fixes:
            text = tomlkit.dumps(document)
        values = document.unwrap()
    return ContentCheck(text, _semantic_issues(values, text), fixes)
