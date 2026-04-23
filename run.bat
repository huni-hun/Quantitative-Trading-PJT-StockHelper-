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
echo  - 대시보드: http://localhost:5000
echo  - 대시보드가 완전히 뜬 뒤 매매봇이 자동 시작됩니다.
start "StockHelper Dashboard" cmd /k "set PYTHONPATH=& set PYTHONHOME=& cd /d %~dp0& call venv\Scripts\activate.bat& python web\app.py"
echo 대시보드 초기화 대기중 (3초)...
timeout /t 3 >nul
set STOCKHELPER_SUBPROCESS=1
set DASHBOARD_URL=http://127.0.0.1:5000
python main.py
goto END

:END
pause
