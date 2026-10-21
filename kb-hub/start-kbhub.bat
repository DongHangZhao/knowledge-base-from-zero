@echo off
rem ============================================================
rem  KB Hub Portal launcher (ASCII only -> no GBK quote-mangling)
rem  Starts the local portal on 127.0.0.1 and opens the browser.
rem
rem  Python discovery order:
rem    1) %KBHUB_PY%  (set this to any python.exe you prefer)
rem    2) python.exe next to this script (portable python)
rem    3) py -3  /  python  on PATH
rem
rem  Port: tries 8799, then 8800-8808 (server also auto-shifts if busy).
rem  Usage: double-click this file.
rem ============================================================
setlocal enabledelayedexpansion
title KB-Hub-Portal
set "ROOT=%~dp0"
set "SERVER=%ROOT%kb_hub_server.py"

if not exist "%SERVER%" (
  echo [ERROR] server not found: %SERVER%
  pause
  exit /b 1
)

echo [1/4] locating python ...
set "PY="
if defined KBHUB_PY if exist "%KBHUB_PY%" set "PY=%KBHUB_PY%"
if not defined PY if exist "%ROOT%python.exe" set "PY=%ROOT%python.exe"
if not defined PY (
  where py >nul 2>&1
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
  where python >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo [ERROR] Python not found.
  echo         Install Python 3.9+  OR  download kb-hub.exe from Releases
  echo         OR set KBHUB_PY=C:\path\to\python.exe
  pause
  exit /b 1
)
echo       using: %PY%

echo [2/4] picking a free port (8799-8808) ...
set "PORT="
for %%P in (8799 8800 8801 8802 8803 8804 8805 8806 8807 8808) do (
  if not defined PORT (
    netstat -ano | findstr ":%%P " | findstr "LISTENING" >nul 2>&1
    if errorlevel 1 set "PORT=%%P"
  )
)
if not defined PORT (
  echo [ERROR] ports 8799-8808 are all in use.
  pause
  exit /b 1
)
echo       port: %PORT%

echo [3/4] starting service in a new window ...
start "KB-Hub" %PY% "%SERVER%" --port %PORT% --no-browser
echo       (keep that KB-Hub window open = service is running)

echo [4/4] waiting for /api/health ...
set "READY=N"
for /l %%i in (1,1,25) do (
  if /i not "!READY!"=="Y" (
    powershell -NoProfile -Command "try { if ((Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/api/health' -TimeoutSec 1 -UseBasicParsing).StatusCode -eq 200) { 'Y' } else { 'N' } } catch { 'N' }" > "%TEMP%\kbhub_up.txt" 2>nul
    if exist "%TEMP%\kbhub_up.txt" (
      set /p "READY=" < "%TEMP%\kbhub_up.txt"
      del "%TEMP%\kbhub_up.txt" >nul 2>&1
    )
    ping 127.0.0.1 -n 2 >nul
  )
)

echo.
echo  Portal : http://127.0.0.1:%PORT%/
echo  Stop   : close the KB-Hub window
echo ==================================================
if /i "!READY!"=="Y" (
  echo  [OK] service is up - opening browser ...
  start "" "http://127.0.0.1:%PORT%/"
) else (
  echo  [WARN] service not ready; open http://127.0.0.1:%PORT%/ manually
)
echo.
pause
