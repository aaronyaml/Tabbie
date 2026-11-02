@echo off
rem Hermes memory CLI on Windows (see memory\README.md). Stdlib-only Python.
setlocal
set "PYTHONPATH=%~dp0..;%PYTHONPATH%"
set PYTHONUTF8=1
where python >nul 2>nul && (python -m hmem.cli %* & exit /b %errorlevel%)
uv run --no-project -q python -m hmem.cli %*
