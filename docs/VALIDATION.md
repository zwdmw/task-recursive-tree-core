# 发布前验证记录

[返回首页](../README.md) · [GeminiER2 集成说明](INTEGRATION.md)

验证日期：2026-10-09。发布副本在独立目录中整理，保留原工作目录的未提交修改。

## 核心安装与运行

在新建的 Windows / CPython 3.12.10 虚拟环境中执行 README 的安装步骤：

```bash
python -m pip install -e '.[dev]'
python scripts/check_core.py
```

- Editable 安装成功，项目版本 `0.1.0`。
- 正常任务演示返回 `status=succeeded`，退出码 0，写出任务树 JSON。
- 障碍任务演示返回 `status=succeeded`，退出码 0，完成递归恢复并写出任务树 JSON。
- 核心测试集的 121 项测试通过，包含任务树、规划、执行事务、持续会话和 Web 入口。

GeminiER2 / MuJoCo 使用单独安装的 Harness 环境。其原始集成录屏作为效果演示公开，适配器的测试准备见[集成说明](INTEGRATION.md)。本次发布验证针对独立核心入口。

## 视频与页面

- 原始 MP4：26.033333 秒，1920 × 1080，30 fps，H.264 视频和 AAC 音频。
- 仓库视频与用户提供的原文件 SHA256 一致，身份见[视频元数据](media/video.json)。
- 在线播放页经过浏览器加载与实际播放检查。
- 桌面与 390 px 手机宽度均检查排版，手机页面无横向溢出。

## 公开 CI

[Core runtime checks](../.github/workflows/core.yml) 在 Ubuntu / Windows、Python 3.11 / 3.12 上安装工程、执行核心检查并构建 wheel。实际运行结果由仓库 Actions 页面记录。
