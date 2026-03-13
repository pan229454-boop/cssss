@echo off
chcp 65001 >nul
title NAT Tunnel 命令行客户端

echo ============================================
echo   NAT Tunnel 命令行客户端 - Windows
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python
    pause
    exit /b 1
)

pip install pyyaml >nul 2>&1

echo 请先编辑 config.yaml 填写服务器信息
echo.

python "%~dp0client.py"
pause
