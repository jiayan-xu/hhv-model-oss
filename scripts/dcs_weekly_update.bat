@echo off
REM DCS 小时数据周更：增量拉最近数日 + 重跑热值反推
setlocal
cd /d C:\Users\user\hhv-model
set LOGDIR=C:\Users\user\hhv-model\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set TS=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%
set TS=%TS: =0%
set LOG=%LOGDIR%\dcs_weekly_%TS%.log

echo [%DATE% %TIME%] snmis login >> "%LOG%"
"C:\Python314\python.exe" scripts\snmis_login.py >> "%LOG%" 2>&1
if ERRORLEVEL 1 (
  echo [%DATE% %TIME%] login failed, abort >> "%LOG%"
  exit /b 1
)

echo [%DATE% %TIME%] start weekly DCS pull >> "%LOG%"
"C:\Python314\python.exe" scripts\pull_dcs_hourly.py --days 8 >> "%LOG%" 2>&1
set PULL_RC=%ERRORLEVEL%
echo [%DATE% %TIME%] pull rc=%PULL_RC% >> "%LOG%"

if %PULL_RC%==0 (
  echo [%DATE% %TIME%] run hhv model >> "%LOG%"
  "C:\Python314\python.exe" run.py --config config.snmis_daily.yaml >> "%LOG%" 2>&1
  echo [%DATE% %TIME%] model rc=%ERRORLEVEL% >> "%LOG%"
) else (
  echo [%DATE% %TIME%] skip model, pull failed >> "%LOG%"
)

echo [%DATE% %TIME%] done >> "%LOG%"
exit /b %PULL_RC%
