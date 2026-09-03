@echo off
chcp 65001 >nul
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Task Recursive Tree + GeminiER2 Realtime Voice

set "PYTHON_EXE="
set "PYTHON_ARGS="
set "HARNESS_ROOT=%~d0\GeminiER2Harness"
set "PROJECT_PYTHON=%CD%\.venv\Scripts\python.exe"
set "HARNESS_PYTHON=%HARNESS_ROOT%\.venv\Scripts\python.exe"

if not exist "%HARNESS_ROOT%\er2sim\web\voice_control.js" (
    echo [ERROR] Harness voice control resources are missing:
    echo         %HARNESS_ROOT%\er2sim\web\voice_control.js
    if /i not "%TRT_NO_PAUSE%"=="1" pause
    exit /b 1
)
if not exist "%HARNESS_ROOT%\er2sim\web\voice_control.css" (
    echo [ERROR] Harness voice control resources are missing:
    echo         %HARNESS_ROOT%\er2sim\web\voice_control.css
    if /i not "%TRT_NO_PAUSE%"=="1" pause
    exit /b 1
)

if exist "%PROJECT_PYTHON%" (
    "%PROJECT_PYTHON%" -c "import sys, faster_whisper, opencc; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=%PROJECT_PYTHON%"
        goto :python_ready
    )
)

if exist "%HARNESS_PYTHON%" (
    "%HARNESS_PYTHON%" -c "import sys, faster_whisper, opencc; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=%HARNESS_PYTHON%"
        goto :python_ready
    )
)

where py.exe >nul 2>&1
if not errorlevel 1 (
    py.exe -3 -c "import sys, faster_whisper, opencc; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=py.exe"
        set "PYTHON_ARGS=-3"
        goto :python_ready
    )
)

:python_ready
if not defined PYTHON_EXE (
    echo [ERROR] Python 3.11 or newer with local voice dependencies was not found.
    echo.
    echo Checked:
    echo     %PROJECT_PYTHON%
    echo     %HARNESS_PYTHON%
    echo     py.exe -3
    echo.
    echo Install the voice dependencies with:
    echo     "%HARNESS_PYTHON%" -m pip install -r "%HARNESS_ROOT%\requirements-voice.txt"
    echo.
    if /i not "%TRT_NO_PAUSE%"=="1" pause
    exit /b 1
)

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%CD%\src"
set "GEMINI_ER2_HARNESS_ROOT=%HARNESS_ROOT%"
if not defined HF_ENDPOINT set "HF_ENDPOINT=https://hf-mirror.com"
if not defined HF_HUB_DISABLE_XET set "HF_HUB_DISABLE_XET=1"

echo Cleaning up the previous task-tree server instance...
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" ^
    -NoLogo ^
    -NoProfile ^
    -ExecutionPolicy Bypass ^
    -File "scripts\stop_tree_server.ps1" ^
    -ProjectRoot "%CD%"
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to clean up the previous task-tree server.
    if /i not "%TRT_NO_PAUSE%"=="1" pause
    exit /b 1
)
echo.

echo Starting the GeminiER2 recursive task-tree session with realtime voice...
echo Python: %PYTHON_EXE% %PYTHON_ARGS%
echo Kernel: TaskTreeKernel
echo Harness: %HARNESS_ROOT%
echo The MuJoCo viewer and browser console will open automatically.
echo Click Start Listening once to grant microphone access.
echo The Realtek microphone is preferred automatically when available.
echo Speech is transcribed locally with Faster-Whisper.
echo Showcase scene: recursive_recovery_showcase
echo Enter your own task in the browser instruction box.
echo The MuJoCo viewer starts with an interactive free camera.
echo Say "stop" or "pause" to stop, and "continue" to resume.
echo Text controls and all existing task-tree features remain available.
echo Stop cancels the current action and preserves the live scene.
echo Resume and later instructions continue from the observed world state.
echo Press Ctrl+C here to close the server and simulation.
echo.

"%PYTHON_EXE%" %PYTHON_ARGS% -m task_recursive_tree.integrations.gemini_er2.server ^
    --brain llm ^
    --view ^
    --realtime ^
    --record ^
    --voice-control ^
    --open-browser ^
    --scene recursive_recovery_showcase ^
    --viewer-camera free ^
    %*

set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" (
    echo [ERROR] Voice task-tree server exited with code %EXIT_CODE%.
    if /i not "%TRT_NO_PAUSE%"=="1" (
        echo.
        pause
    )
)
exit /b %EXIT_CODE%
