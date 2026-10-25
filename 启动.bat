@echo off
setlocal enabledelayedexpansion
title 文科论文写作助手

echo ================================================
echo   文科论文写作助手 - 启动
echo ================================================
echo.

set "ROOT=%~dp0"
cd /d "%ROOT%"

rem ---------- 1. 找 Python 3.11+ ----------
set "PY="
rem 首选：项目专用 conda 环境（配置者已建好）
if exist "D:\Environment\conda\envs\law_paper\python.exe" set "PY=D:\Environment\conda\envs\law_paper\python.exe" & goto :found_py
rem 其次：py 启动器
where py >nul 2>nul
if errorlevel 1 goto :try_path_python
for /f "delims=" %%v in ('py -3.12 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%v"
if not "!PY!"=="" goto :found_py
for /f "delims=" %%v in ('py -3.11 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%v"
if not "!PY!"=="" goto :found_py
for /f "delims=" %%v in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%v"
if not "!PY!"=="" goto :found_py
:try_path_python
rem 最后：PATH 里的 python
where python >nul 2>nul
if errorlevel 1 goto :no_python
for /f "delims=" %%v in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%v"
if not "!PY!"=="" goto :found_py
:no_python
echo [错误] 未找到 Python。请先安装 Python 3.11+，或创建专用环境：
echo   conda create -n law_paper python=3.12 -y
echo   然后安装依赖：python -m pip install -r requirements.txt
echo.
pause
exit /b 1

:found_py
echo [1/3] Python: !PY!
"!PY!" -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 goto :py_too_old
goto :check_deps

:py_too_old
echo [错误] Python 版本过低，需要 3.11+。当前版本：
"!PY!" --version
pause
exit /b 1

:check_deps
rem ---------- 2. 检查并安装依赖 ----------
"!PY!" -c "import fastapi, uvicorn, httpx, fitz, docx, numpy, sqlite_vec" >nul 2>nul
if errorlevel 1 goto :install_deps
echo [2/3] 依赖已就绪
goto :find_port

:install_deps
echo [2/3] 依赖缺失，正在安装（首次需要联网，请稍候）...
"!PY!" -m pip install -r requirements.txt
if errorlevel 1 goto :deps_failed
goto :find_port

:deps_failed
echo [错误] 依赖安装失败。请检查网络后重试，或手动执行：
echo   "!PY!" -m pip install -r requirements.txt
pause
exit /b 1

:find_port
rem ---------- 3. 找端口 8000-8010 并启动 ----------
set "PORT=8000"
for /l %%p in (8000,1,8010) do (
    netstat -ano | findstr /r /c:":%%p .*LISTENING" >nul 2>nul
    if errorlevel 1 set "PORT=%%p" & goto :start
)
echo [错误] 端口 8000-8010 全部被占用。请关闭占用程序，或设置环境变量 PORT 指定端口。
pause
exit /b 1

:start
echo [3/3] 启动后端： http://127.0.0.1:!PORT!
echo       关闭本窗口即停止程序（数据已保存在 data 目录，不会丢）。
echo.
echo   正在打开浏览器...
start "" http://127.0.0.1:!PORT!
"!PY!" -X utf8 -m backend.main

echo.
echo 程序已停止。
pause
