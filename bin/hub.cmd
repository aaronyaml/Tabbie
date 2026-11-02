@echo off
rem hub on Windows: runs bin\hub.py with Tabbie's Python (uv), or any python on PATH.
setlocal
set "HUB=%~dp0hub.py"
where uv >nul 2>nul && (uv run --no-project -q --with ruamel.yaml python "%HUB%" %* & exit /b %errorlevel%)
if exist "%USERPROFILE%\.local\bin\uv.exe" ("%USERPROFILE%\.local\bin\uv.exe" run --no-project -q --with ruamel.yaml python "%HUB%" %* & exit /b %errorlevel%)
python "%HUB%" %*
