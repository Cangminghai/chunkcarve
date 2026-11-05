@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
set "PYW="

rem 1) Check virtualenv
if exist "%~dp0.venv\Scripts\pythonw.exe" set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"

rem 2) Check pythonw in PATH with webview
if not defined PYW (
  for /f "delims=" %%i in ('where pythonw 2^>nul') do (
    if not defined PYW (
      "%%i" -c "import webview" >nul 2>nul && set "PYW=%%i"
    )
  )
)

rem 3) Check local appdata python
if not defined PYW if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not defined PYW if exist "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"

rem 4) Launch Desktop Window if pythonw with webview exists
if defined PYW (
  start "" "%PYW%" desktop.pyw
  exit /b 0
)

rem 5) Fallback: Launch in Browser mode
if not defined PY set "PY=python"
echo [ChunkCarve] Starting server in browser mode...
"%PY%" -m mcmap.server
pause
