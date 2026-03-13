@echo off
chcp 65001 >nul
title NAT Tunnel 内网穿透客户端

echo ============================================
echo   NAT Tunnel 内网穿透客户端 - Windows
echo ============================================
echo.

:: 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.7+
    echo 下载地址: https://www.python.org/downloads/
    echo.
    echo 安装时请勾选 "Add Python to PATH"
    pause
    exit /b 1
)

:: 安装依赖
echo [1/2] 检查依赖...
pip install pyyaml >nul 2>&1

:: 启动GUI
echo [2/2] 启动客户端...
echo.
python "%~dp0client_gui.py"

if errorlevel 1 (
    echo.
    echo 启动失败，尝试命令行模式...
    python "%~dp0client.py"
)

pause
