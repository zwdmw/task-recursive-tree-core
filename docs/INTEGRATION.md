# GeminiER2Harness / MuJoCo 集成

[返回首页](../README.md) · [完整运行参考](runtime-reference.md)

效果视频展示的是核心任务树接入 GeminiER2Harness 的运行。核心任务树的 CLI 和浏览器演示使用本仓库的平面参考后端；MuJoCo 场景、视觉与自然语言规划、录制和语音控制由单独安装的 Harness 环境提供。

## 准备集成环境

先准备兼容的 GeminiER2Harness 工程，并按该工程的说明安装其仿真、模型和语音依赖。Task Recursive Tree 通过 Harness 根目录内的 `er2sim/task_compiler.py` 识别环境，加载 `er2sim` 模块和网页资源。服务配置、模型凭据与语音模型由 Harness 环境管理。

再安装本仓库：

```bash
python -m pip install -e .
```

显式指定 Harness 路径，便于把工程放在任意目录：

```bash
export GEMINI_ER2_HARNESS_ROOT=/path/to/GeminiER2Harness
trt-gemini-er2-server --harness-root "$GEMINI_ER2_HARNESS_ROOT" --no-browser
```

```powershell
$env:GEMINI_ER2_HARNESS_ROOT = 'X:\your-projects\GeminiER2Harness'
trt-gemini-er2-server --harness-root $env:GEMINI_ER2_HARNESS_ROOT --no-browser
```

使用默认端口 `8766`，也可通过 `--port` 指定。每次任务经 planner adapter、规范化 `TaskProgram`、compiler bridge，再交给 `TaskTreeStore` / `TaskTreeKernel` 执行。Harness 提供物理后端和世界观测。

Windows 的 `启动任务递归树.cmd` 为原始配套环境保留，默认寻找同盘根目录下的 `GeminiER2Harness`，并检查语音网页资源与 Faster-Whisper / OpenCC。显式命令便于采用其他工程目录和依赖组合。

## 测试集成适配器

```bash
python -m pip install -e '.[dev]'
python -m pytest tests -q
```

全部测试中既有 fake Harness 契约测试，也有读取真实 Harness 模块的测试。核心检查 `python scripts/check_core.py` 使用仓库内后端，不访问外部模型服务。

视频与集成说明帮助理解接入过程。核心检查的发布验收记录见 [VALIDATION](VALIDATION.md)。
