@echo off
rem ====================================================================
rem  一键启动完整座舱仿真（动力域 ECU + 座舱仪表 GUI + 安卓车机 GUI）
rem
rem  用法：
rem    start_cockpit.bat                     默认（车速0 水温90）
rem    start_cockpit.bat 60                  车速 60
rem    start_cockpit.bat 60 125              车速60 + 水温125（触发故障）
rem
rem  实际逻辑在 start_cockpit.py（跨平台，bat 只是转发）；
rem  Windows 上两个 GUI 会有独立控制台窗口。
rem  停止：在主窗口按 Ctrl+C
rem ====================================================================
chcp 65001 >nul
cd /d %~dp0

set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe

%PY% start_cockpit.py %1 %2
pause
