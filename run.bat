@echo off
chcp 65001 >nul
cd /d %~dp0

:: QGIS 등 외부 Python 환경 간섭 차단
set PYTHONPATH=
set PYTHONHOME=

call venv\Scripts\activate.bat

set choice=

echo ========================================
echo   StockHelper 실행 메뉴
echo ========================================
echo   1. 웹 대시보드 (web/app.py)
echo   2. 매매봇 직접 실행 (main.py)
echo   3. 웹 대시보드 + 매매봇 동시 실행
echo ========================================
set /p choice=번호를 선택하세요 (1/2/3):

if /i "%choice%"=="1" goto DASHBOARD
if /i "%choice%"=="2" goto BOT
if /i "%choice%"=="3" goto BOTH
echo 잘못된 선택입니다.
goto END

:DASHBOARD
echo [웹 대시보드 시작] http://localhost:5000
python web\app.py
goto END

:BOT
echo [매매봇 시작]
python main.py
goto END

:BOTH
echo [웹 대시보드 + 매매봇 동시 시작]
start "StockHelper Dashboard" cmd /k "set PYTHONPATH=& set PYTHONHOME=& cd /d %~dp0& call venv\Scripts\activate.bat& python web\app.py"
timeout /t 2 >nul
python main.py
goto END

:END
pause
