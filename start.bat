@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title LLM Qbank Bridge

rem ------------------------------------------------------------
rem  本脚本只负责启动服务，不打开任何页面。
rem  服务程序本身也不会开浏览器，请手动复制下面的地址打开。
rem ------------------------------------------------------------

set "APP=qbank_bridge.py"
set "PY="

rem ---- 1) PATH 里的 python：可能有多个，逐个试跑，跳过商店占位 exe ----
for /f "delims=" %%I in ('where python 2^>nul') do call :try_py "%%I"

rem ---- 2) PATH 里的 py 启动器 ----
if not defined PY call :try_py py -3
if not defined PY call :try_py py

rem ---- 3) WorkBuddy 自带的 python ----
if not defined PY (
  for /d %%V in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
    if not defined PY call :try_py "%%~V\python.exe"
  )
)

rem ---- 4) 常见安装位置 ----
if not defined PY (
  for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%USERPROFILE%\anaconda3\python.exe"
    "%LOCALAPPDATA%\anaconda3\python.exe"
    "%ProgramData%\anaconda3\python.exe"
    "C:/Python313/python.exe"
    "C:/Python312/python.exe"
    "D:/software/ANACONDA/python.exe"
  ) do (
    if not defined PY if exist "%%~P" call :try_py "%%~P"
  )
)

if not defined PY goto no_py

if not exist "%APP%" (
  echo.
  echo   [启动失败] 当前目录下没找到 %APP%
  echo   请把 start.bat 放在 %APP% 同一个文件夹里再运行。
  echo.
  pause
  exit /b 1
)

echo.
echo   ==================================================
echo     服务地址：http://127.0.0.1:8765
echo     本窗口只负责启动服务，不会打开页面
echo     要看界面，请把上面的地址复制到浏览器打开
echo     关掉这个黑窗口，服务就停了
echo   ==================================================
echo.
echo   使用解释器：%PY:"=%
echo   正在启动服务，日志会直接显示在下面...
echo.

rem ---- 前台运行：日志留在本窗口，关窗口即停服务 ----
%PY% "%APP%"
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" goto exit_ok
if "%RC%"=="3" goto exit_already
if "%RC%"=="9009" goto exit_9009
echo   服务异常退出，返回码 %RC%
echo   上面若有报错信息，截图发我即可。
goto exit_bye

:exit_ok
echo   服务已停止。
goto exit_bye

:exit_already
echo   服务本来就在运行，这次没有重复启动。
goto exit_bye

:exit_9009
echo   [启动失败] Python 没能执行，返回码 9009。
echo   多半是 PATH 里那个微软商店的 python.exe 占位文件在捣乱。
echo   请把真实 python.exe 的完整路径填到本文件 set "PY=" 那一行，
echo   例如：set "PY=D:/software/ANACONDA/python.exe"
pause
exit /b 1

:exit_bye
echo   按任意键关闭窗口。
pause >nul
exit /b %RC%

:no_py
echo.
echo   [启动失败] 这台电脑上没有可用的 Python。
echo   注意：微软商店那个 python.exe 是占位文件，不算装好。
echo   办法一：安装 Python 3.8+，安装时勾选 "Add python.exe to PATH"
echo   办法二：把 python.exe 完整路径填到本文件 set "PY=" 那一行
echo.
pause
exit /b 1

rem ---- 子程序：试一个候选解释器，能跑通 -V 才采用 ----
:try_py
if defined PY goto :eof
if "%~1"=="" goto :eof
echo %~1|findstr /i /c:"\WindowsApps\" >nul && goto :eof
%* -V >nul 2>&1
if errorlevel 1 goto :eof
set "PY=%*"
goto :eof
