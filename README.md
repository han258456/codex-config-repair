# Codex 配置修复助手

一个使用 **Python + PySide6（Qt 6）** 编写的中文 Windows 桌面工具。修复配置内容、合并旧配置、处理历史记录兼容问题，并支持 OpenAI 直连与第三方中转切换。

**Windows 10/11 x64 · Python 3.11+ · Qt 6 · MIT**

这是独立的社区工具，与 OpenAI 无隶属关系。

[GitHub 仓库](https://github.com/han258456/codex-config-repair) · [Gitee 仓库](https://gitee.com/hangev8/codex-config-repair) · [GitHub 发行版](https://github.com/han258456/codex-config-repair/releases) · [Gitee 发行版](https://gitee.com/hangev8/codex-config-repair/releases) · [更新日志](CHANGELOG.md)

## 给普通使用者

Windows 便携包由维护者手动上传到 [GitHub 发行版](https://github.com/han258456/codex-config-repair/releases)或 [Gitee 发行版](https://gitee.com/hangev8/codex-config-repair/releases)。若暂未看到附件，可按下文从源码运行。

1. 解压 `CodexConfigRepair-1.2.0-windows-x64.zip`，保留整个文件夹。
2. 双击其中的 `CodexConfigRepair.exe`。无需安装 Python。
3. 选择 Codex 数据目录，通常是 `%USERPROFILE%\.codex`；选择旧配置文件（可选）。
4. 点击 **扫描并预览**，查看待处理会话、可恢复项目和脱敏配置差异。
5. 保存工作并退出 Codex 桌面程序和命令行，再点击 **备份并修复**。
6. 完成后重新打开 Codex。

不要只单独复制 EXE；同目录的 `_internal` 是运行依赖。向其他人提供软件时，发送完整 ZIP 即可。源码包可以独立使用。

## 切换 OpenAI 直连与第三方中转

1. 在“检查与修复”页选择要处理的 Codex 数据目录，然后进入 **连接切换**。
2. 选择 **OpenAI 直连**：恢复官方默认地址，沿用已有 OpenAI 登录；没有登录时，在 Codex 内完成登录。
3. 或选择 **第三方中转**：填写服务商提供的 **API 基础地址**（例如 `https://gateway.example.com/v1`）和 **API 密钥**。中转必须支持 **Responses API**。不要在地址后填写 `/responses` 或 `/chat/completions`。
4. 如中转的模型名称不同，可填写“模型名称”；留空保留当前模型。切回直连时，也可填写官方支持的模型名称。
5. 默认同步兼容历史记录。点击 **预览切换**，查看脱敏差异，退出 Codex 后点击 **备份并切换**。
6. 重新打开 Codex 后生效。在“备份与恢复”中可以回滚本次切换；多次连续切换时按从新到旧的顺序恢复。

已保存的中转地址会自动回填。地址不变时，密钥留空可沿用保存值；输入新密钥会替换原值。更换地址必须填写对应的密钥。仅更新密钥时，预览会提示敏感配置已更新，不展示内容。

**密钥存储：** 为方便独立使用，本工具使用 Codex 支持的 `experimental_bearer_token`，密钥以明文写入本机 `config.toml` 中的专用 `model_providers.codex_repair_relay` 配置。输入框默认隐藏密钥，报告自动脱敏；原始配置和备份仍可能包含密钥，请在本人电脑保管。切回直连后保留中转设置，供下次切换使用。此存储方式不是加密保险箱；官方更推荐由环境变量提供密钥，已自行设置环境变量的用户也可继续通过原有配置管理。

工具不读取或改写 `auth.json`，不发送测试请求。配置校验通过仅说明本地写入和历史兼容处理成功；地址可用性、密钥权限和模型支持需在 Codex 中实际验证。系统环境变量、启动参数或独立 profile 可能覆盖主配置，需在对应环境中检查。

## 修复配置文件内容

当 `config.toml` 语法损坏、复制内容带有 Markdown 代码围栏，或需要手动修改配置时，可以使用左侧第 3 项 **配置内容修复**。原文件无法解析也能使用此页面。

1. 在“检查与修复”页选择 Codex 数据目录，再进入 **配置内容修复**。
2. 读取当前配置，或导入一个 TOML 文件作为待保存内容。编辑区默认展示脱敏内容并保持只读。
3. 需要手动编辑时，勾选 **显示并编辑原文（可能含密钥）**，在本机查看并修改完整内容。导入与编辑均不会立即覆盖当前文件。
4. 点击 **检查并预览**，查看错误、提示和脱敏差异；也可以点击 **自动修复并预览**，生成有限范围内的修复内容后重新检查。
5. 内容通过检查后，保存工作并退出 Codex，点击 **备份并保存**。软件先备份当前 `config.toml` 的完整原始字节，再保存候选内容。
6. 完成后重新打开 Codex。需要回退时，在 **备份与恢复** 页面恢复本次备份；也可用 **导出检测报告** 分享脱敏检查结果。

**自动修复范围：** 仅处理包裹完整配置的 Markdown 代码围栏、作为字符串分隔符的弯双引号、布尔值 `True` / `False` 的大小写，以及已明确识别的布尔字段中的字符串 `"true"` / `"false"`。不会删除重复键，不猜测提供方、模型或安全权限，不修改未知字段；不能确定的内容需手动处理。自动修复后仍须检查并预览，保存必须由使用者点击执行。

本页面只修改 `config.toml`，不会自动迁移历史记录。若编辑更改了 `model_provider`，保存后可到 **检查与修复** 页面同步历史记录标记。手动输入或导入的内容按使用者选择保存，不会自动合并原文件中的其他设置。

保存时保留原文件的 UTF-8 BOM 和换行方式；识别到带 BOM 的 UTF-16 文件时转换为 UTF-8，原始编码文件仍完整保存在备份中。单个配置文件和候选内容限 2 MB。若当前文件在读取或预览后被其他程序修改，软件会停止保存，需重新读取并预览。

检查仅覆盖 TOML 语法与部分常见配置项，在本机完成；不验证网络连通性、密钥权限，也不能保证涵盖 Codex 所有版本和未来新增字段。原文编辑区和原始备份可能含密钥，检测报告与差异预览自动脱敏。

## 功能与默认行为

| 功能 | 行为 |
| --- | --- |
| 普通修复 | 保留当前主配置的连接、模型和认证设置 |
| 连接切换 | 单独页面选择直连或中转，支持地址、隐藏的密钥输入和可选模型；切换自动备份 |
| 配置内容修复 | 独立页面检查、有限自动修复或手动编辑 TOML；默认脱敏只读，先备份再保存，仅修改配置文件 |
| 配置合并 | 当前值优先，只补充旧配置缺失的已信任项目及支持的桌面偏好 |
| 自定义 MCP | 默认不导入；勾选后补充缺失工具及其配置，跳过过时的内置运行工具和本机不存在的绝对程序路径 |
| 历史兼容 | 将旧提供方标记迁移到当前提供方，覆盖活动与归档会话 |
| 分页索引 | 同步修正 SQLite 字节索引及 `history_base` 分支引用；支持字节长度和数字位数变化 |
| 原文保护 | 不修改会话 JSONL 首行之后的正文，逐文件校验 SHA-256 |
| 备份 | 写入前保存原文件、SQLite 在线快照、变更清单和校验结果 |
| 恢复 | 只回滚本次文件及数据库字段；保留无关新增会话，遇到相关文件新增内容时停止 |
| 隐私 | 本地离线处理，不读取 `auth.json`，不连接模型或 MCP 服务器；报告隐藏凭据 |

当前版本支持默认目录内的 `state_<版本>.sqlite` 与已识别结构的 `thread_history_<版本>.sqlite`，以及传统 JSONL 历史。独立 profile、外置 `sqlite_home`、未知数据库结构、缺失历史文件等情况会停止历史迁移。可关闭“兼容旧历史记录”单独合并配置。

本工具不修改历史会话的模型参数或加密推理内容，也不执行模型续聊。不同提供方的模型能力、加密上下文及账号权限可能需要在 Codex 内另行处理。配置检查不是模型接口连通性测试。

## 备份与恢复

备份保存在所选数据目录的 `repair-backups/<时间>-<标识>/`：

**无需提前做备份，也不要求存在 `config.toml-bf`。** 点击“备份并修复”“备份并切换”或“备份并保存”时，软件自动创建独立目录，先备份并校验本次涉及的文件与数据库，再执行修改。配置内容修复只备份并修改 `config.toml`；原文件即使格式损坏，仍按原始字节完整备份。若备份失败，则停止写入。打开软件、扫描、编辑和预览不会创建备份。

```text
manifest.json   # 状态、文件哈希、受影响的字段和索引偏移
report.json     # 脱敏修复摘要（成功后生成）
originals/      # 本次修改文件的完整原件
databases/      # 写入前数据库快照
staged/         # 写入前暂存文件
```

到 **备份与恢复** 页选择备份，退出 Codex 后点击 **恢复所选备份**。原始备份可能包含旧配置中的密码，应留在本人电脑；对外沟通使用“导出检测报告”。

写入错误会尝试自动回滚。进程或电脑意外退出后，未完成的备份会显示“待处理”“中断待恢复”或“需恢复”，应先恢复再重试。恢复前会校验原始备份及当前文件；若已产生新内容，工具不会用整库快照覆盖新数据。

备份页只列出本软件生成的备份，不自动导入手工脚本产生的旧备份。

## 从源码运行

要求 Windows 10/11 x64、Python 3.11 或更高版本。

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

演示模式会创建独立临时目录，使用虚构数据；结束后清理。若在演示界面改选真实目录，仍会执行正常的 Codex 运行状态检查。

```powershell
.\.venv\Scripts\python.exe main.py --demo
```

## 测试与打包

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe main.py --smoke-test --screenshot artifacts\preview.png
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe scripts\build_windows.py
```

输出：

- `dist/CodexConfigRepair/CodexConfigRepair.exe`：可直接启动的程序。
- `release/CodexConfigRepair-1.2.0-windows-x64.zip`：发给他人的完整便携包。
- `release/CodexConfigRepair-1.2.0-source.zip`：源码、测试和构建说明。
- `release/SHA256SUMS.txt`：发布包哈希。

GUI 烟雾测试会在模拟数据上实际点击扫描、修复、恢复、中转切换、直连切换和切换恢复按钮，并检查配置内容修复、进程拦截和换地址密钥校验，不会修改真实用户配置。内容修复测试覆盖原文件损坏、手工编辑、有限自动修复、自动备份、恢复、编码转换与脱敏。可对打包后的程序执行相同的 `--smoke-test` 检查。

本机已安装 Codex 命令行时，还可以运行 `scripts/verify_codex_config.py --codex <codex.exe的路径>`，使用独立临时配置验证 Codex 的严格配置解析。此检查只读取模拟配置，不发送模型请求。

## 代码结构

```text
main.py                 程序入口、演示和 GUI 烟雾测试
codex_repair/engine.py   配置合并、历史迁移、备份与恢复
codex_repair/content.py  配置内容检查和有限自动修复
codex_repair/window.py   Qt 界面和后台工作线程
codex_repair/demo.py     完全虚构的测试数据
tests/test_engine.py    迁移、回滚、冲突和脱敏测试
tests/test_connection.py 连接切换、密钥校验、历史同步和恢复测试
tests/test_content_engine.py 配置内容保存、备份、恢复和脱敏测试
scripts/build_windows.py 便携包和源码包构建
```

## 开源与贡献

本项目自身代码使用 [MIT 协议](LICENSE)。第三方依赖保留各自许可证，详见 [第三方组件说明](THIRD_PARTY_NOTICES.md)。

参与开发请阅读 [贡献指南](CONTRIBUTING.md)，版本变更见 [更新日志](CHANGELOG.md)。仓库只发布源码；Windows 便携包由维护者上传到发行版附件。

问题反馈可提交至 [GitHub Issues](https://github.com/han258456/codex-config-repair/issues) 或 [Gitee Issues](https://gitee.com/hangev8/codex-config-repair/issues)，请勿附带真实密钥、登录文件和原始会话备份。

## 参考资料

- [Codex 配置格式](https://learn.chatgpt.com/docs/config-file/config-reference)
- [PySide6 QThread](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QThread.html)
- [TOML Kit：保留注释和格式](https://tomlkit.readthedocs.io/en/stable/)
- [PyInstaller 便携目录打包](https://pyinstaller.org/en/stable/operating-mode.html)
