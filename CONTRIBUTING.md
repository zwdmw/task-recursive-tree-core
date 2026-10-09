# 参与贡献 / Contributing

欢迎文档改进、问题报告、测试补充和运行时改进。中文与英文均可。

## 本地开发

```bash
python -m venv .venv
# Linux / macOS: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
python scripts/check_core.py
git diff --check
```

修改任务树、执行器或恢复逻辑时，检查正常与障碍演示、对应测试和事件记录。GeminiER2 相关修改按[集成说明](docs/INTEGRATION.md)验证，并注明使用的外部环境版本。

## 报告与提交

- 问题报告附 Python / 系统版本、可复制命令、预期与实际结果。
- 提供最小任务树或相关事件片段，并移除令牌、服务密钥和个人日志。
- 每次 PR 聚焦一项变更，说明行为、验证方式和结果；用户可见变更记录在 [CHANGELOG](CHANGELOG.md)。
- 保留版权与许可信息；原创贡献按 MIT 许可提交。

## English checklist

Create a focused branch, install `.[dev]`, run `python scripts/check_core.py` and `git diff --check`, and describe the changed behavior and observed results. Adapter changes also need the appropriate external Harness environment. Keep credentials and personal logs out of submissions. Original contributions use the project MIT license.
