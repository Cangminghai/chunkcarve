@echo off
rem ChunkCarve - Portable Windows Launcher (Aliases to 启动.bat)
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0启动.bat" %*
