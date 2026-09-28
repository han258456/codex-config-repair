# 参与贡献

欢迎提交 Issue 和 Pull Request。此工具会修改用户的本地配置与历史索引，请用模拟数据开发和验证。

## 开发环境

Windows 10/11 x64、Python 3.11 或更高版本。

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe main.py --demo
```

## 提交前验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe main.py --smoke-test --screenshot artifacts\preview.png
```

更改配置合并、连接切换、历史迁移或恢复逻辑时，请补充对应的行为测试，包括失败后原始数据保持完整的情况。保持“先备份和校验，再写入”的顺序，并确保报告与错误信息不泄露凭据。

## 报告问题

请提供软件版本、Windows 版本、复现步骤和界面错误提示。配置问题优先使用软件导出的脱敏检测报告。

不要上传真实 `config.toml`、`auth.json`、会话文件、数据库或原始备份。报告中仍可能出现项目名称或路径，提交前请自行确认可公开的范围。

## 发布约定

- Git 仓库只维护源码、测试、文档、应用图标及许可证。
- `build/`、`dist/`、`release/`、`artifacts/` 和个人配置不纳入版本控制。
- Windows 构建使用 `scripts/build_windows.py`，先通过打包程序的模拟测试，再生成 ZIP 和 SHA-256 校验文件。
- 发布时创建对应版本标签，再将便携 ZIP 与校验文件作为发行版附件上传，不把二进制压缩包提交到 Git 历史。

项目自身代码使用 MIT 协议。第三方组件保留各自的版权和许可证，详见 `THIRD_PARTY_NOTICES.md`。
