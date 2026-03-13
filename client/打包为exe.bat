@echo off
chcp 65001 >nul
title NAT Tunnel - 打包为exe

echo ============================================
echo   NAT Tunnel 打包工具
echo   将客户端打包为单个exe文件
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python
    pause
    exit /b 1
)

echo [1/2] 安装打包工具...
pip install pyinstaller pyyaml

echo.
echo [2/2] 打包中...
cd /d "%~dp0"
pyinstaller --onefile --windowed --name "NAT穿透客户端" --icon=NUL client_gui.py

echo.
if exist "dist\NAT穿透客户端.exe" (
    echo ============================================
    echo   打包成功！
    echo   文件位置: %~dp0dist\NAT穿透客户端.exe
    echo ============================================
    echo.
    echo 将 "NAT穿透客户端.exe" 复制到任意Windows电脑即可运行
    echo 无需安装Python环境
) else (
    echo 打包失败，请检查错误信息
)

pause
