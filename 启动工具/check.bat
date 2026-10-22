@echo off
rem ============================================================
rem  Campus AI Assistant - Environment check (pure ASCII)
rem  Only prints info. Starts nothing.
rem
rem  AUTO-DETECTS the project root by walking up, so it works
rem  whether placed in the project root or in a sub-folder.
rem ============================================================
setlocal enabledelayedexpansion

set "SCRIPTDIR=%~dp0"
if "%SCRIPTDIR:~-1%"=="\" set "SCRIPTDIR=%SCRIPTDIR:~0,-1%"
set "ROOT=%SCRIPTDIR%"

rem ---- Walk up (max 4 attempts) ----
set "FOUND="
set /a STEP=0
:search
set /a STEP+=1
if exist "!ROOT!\backend\main.py" (
  if exist "!ROOT!\frontend\index.html" (
    set "FOUND=!ROOT!"
    goto :found
  )
)
if !STEP! GEQ 5 goto :found

set "PARENT=!ROOT!"
:trim
set "LAST=!PARENT:~-1!"
if "!LAST!"=="\" goto :trimmed
if "!LAST!"==":" goto :trimmed
set "PARENT=!PARENT:~0,-1!"
if not "!PARENT!"=="" goto :trim
:trimmed
if "!LAST!"=="\" set "PARENT=!PARENT:~0,-1!"
set "ROOT=!PARENT!"
if "!ROOT!"=="" goto :found
goto :search

:found
if defined FOUND set "ROOT=!FOUND!"

echo ============================================================
echo   Environment check
echo ============================================================
echo.
echo [1] Script folder:
echo     %SCRIPTDIR%
echo.
if defined FOUND (
  echo     Project root : !FOUND!
  echo     Detected by walking up !STEP! level^(s^).
) else (
  echo     Project root : NOT FOUND
  echo     Searched upward from: !SCRIPTDIR!
  echo     Need a folder containing BOTH:
  echo         backend\main.py
  echo         frontend\index.html
)
echo.
echo [2] Required folders:
if defined FOUND (
  if exist "!ROOT!\backend\main.py" (echo     backend   OK) else (echo     backend   MISSING)
  if exist "!ROOT!\frontend\index.html" (echo     frontend  OK) else (echo     frontend  MISSING)
) else (
  echo     backend   UNKNOWN ^(root not found^)
  echo     frontend  UNKNOWN ^(root not found^)
)
echo.
echo [3] Python:
rem  Probe the three candidates one by one (for+if would expand early).
set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY where py >nul 2>&1 && set "PY=py"
if not defined PY where python3 >nul 2>&1 && set "PY=python3"
if defined PY (
  echo     found: !PY!
) else (
  echo     NOT FOUND in PATH
  echo     Install Python 3.10+ and tick "Add python.exe to PATH".
)
echo.
echo [4] Python path:
if defined PY (!PY! -c "import sys; print('    ' + sys.executable)") else (echo     n/a)
echo.
echo [5] Backend dependencies:
if defined PY (
  rem  Must list every runtime package, including pandas (Excel import).
  rem  Keep in sync with backend\requirements.txt and start-windows.bat.
  !PY! -c "import fastapi, uvicorn, sqlalchemy, jwt, pydantic_settings, xlrd, openpyxl, httpx; print('    ALL OK')" 2>&1
  if errorlevel 1 echo     MISSING - start-windows.bat will install them for you.
) else (
  echo     n/a
)
echo.
echo [6] Ports in use ^(8000 / 5500^):
netstat -ano | findstr /r /c:"LISTENING" | findstr /c:":8000 " /c:":5500 " || echo     both ports free
echo.
echo [7] Console code page:
chcp
echo.
echo ============================================================
echo   Send a screenshot of everything above.
echo ============================================================
pause
