@echo off
rem ============================================================
rem  Campus AI Assistant - One-click launcher
rem
rem  Goal: works on ANY computer with Python installed, at ANY
rem  folder location, with NO manual editing.
rem
rem  What it does:
rem    1. Auto-detects the project root by walking up.
rem    2. Checks Python (python / py / python3).
rem    3. Checks backend deps; if missing -> installs them
rem       AUTOMATICALLY (with a China pip mirror + retry).
rem    4. Picks free ports if 8000 / 5500 are occupied.
rem    5. Starts backend and frontend, opens the browser,
rem       prints a LAN address for phones.
rem
rem  This .bat is pure ASCII on purpose: a .bat containing
rem  non-ASCII text can crash when the file encoding does not
rem  match the console code page ("double-click and it closes").
rem ============================================================
setlocal enabledelayedexpansion
title Campus AI Assistant

rem ---- 0. Locate project root ----
rem
rem !! WHY THIS IS A "NEAREST FIRST" SEARCH - read before touching !!
rem
rem Real incident: the user kept seeing the SAME backend error traceback
rem (line numbers 147 / 1749) no matter how many times we shipped a fixed
rem package. The code was right; the WRONG TREE was running.
rem
rem The old logic walked UP from %~dp0 for up to 4 levels and took the first
rem folder that contained backend\main.py + frontend\index.html. On a PC
rem that already had an older copy of this project (extremely common: users
rem unzip the new package INSIDE the old folder, or keep the old one next
rem to the new one), the walk could bind to the stale parent tree -- so the
rem freshly unzipped code was never the one that started. Nothing warned
rem about it: the launcher happily printed a root and launched old code.
rem
rem Rules now:
rem   1) If THIS script's own folder already qualifies, use it. Never walk.
rem      (The common case: double-clicking the bat in the unzipped folder.)
rem   2) Only if it does not qualify do we walk up -- and then we SAY SO
rem      loudly, so a wrong pick is visible at a glance.
rem   3) We always read the build tag of the tree we picked and print it.
rem      If the user compares that tag with the one in the package manifest
rem      (the plain-text file listing every file in the zip, whose name is
rem      Chinese and therefore CANNOT be echoed from an ASCII-only .bat),
rem      and the two differ, they immediately know a stale folder was used.
rem
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "FOUND="
set "WALKED="
set /a STEP=0
:search
set /a STEP+=1
if exist "!ROOT!\backend\main.py" (
  if exist "!ROOT!\frontend\index.html" (
    set "FOUND=!ROOT!"
    if !STEP! GTR 1 set "WALKED=1"
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

rem ---- Read the build tag of the tree we selected ----
rem Why: the single most useful piece of information when "my fix did not
rem take effect" is WHICH BUILD am I actually running. We read it from the
rem file the package ships (backend\build.txt) instead of guessing,
rem and we print it right next to the root path so they can be compared
rem against the build named in the package manifest / the zip name.
set "BUILD="
if defined FOUND (
  if exist "!FOUND!\backend\build.txt" (
    set /p BUILD=<"!FOUND!\backend\build.txt"
  )
)

echo ============================================================
echo   Campus AI Assistant - Launcher
echo ============================================================
echo   Script folder: %~dp0
if defined FOUND (
  echo   Project root : !FOUND!
) else (
  echo   Project root : NOT FOUND
)
if defined BUILD echo   Package build: !BUILD!
echo.

rem ---- Loud warning when we had to walk up to a parent folder ----
rem This is exactly the situation that burned us: the user runs the new
rem bat, but a PARENT folder also happens to be a valid project copy, and
rem that older copy is what gets started. Make it impossible to miss.
if defined WALKED (
  echo ************************************************************
  echo   [WARN] This script's own folder is NOT a project root.
  echo          The launcher walked UP and selected:
  echo              !FOUND!
  echo.
  echo          If that is an OLD copy of this project, you are about
  echo          to run outdated code and none of the recent fixes will
  echo          apply. To be safe:
  echo            1. Close all CampusAI windows.
  echo            2. Delete the folder shown above COMPLETELY.
  echo            3. Unzip the new package into a BRAND NEW path that
  echo               has no other copy of this project above it.
  echo            4. Double-click start-windows.bat inside the new
  echo               folder and confirm the "Package build" line above
  echo               matches the build named in the package manifest.
  echo ************************************************************
  echo.
  echo   Continuing in 5 seconds... press Ctrl+C to stop instead.
  timeout /t 5 /nobreak >nul
  echo.
)

if not defined FOUND (
  echo [ERROR] Could not find the project root.
  echo         Searched upward from: %~dp0
  echo.
  echo         The project folder must contain BOTH:
  echo             backend\main.py
  echo             frontend\index.html
  echo.
  echo         Put this .bat anywhere inside the project folder
  echo         and run it again.
  goto :end
)
set "ROOT=!FOUND!"

rem ---- 1. Find Python ----
rem  Probe the three candidates one by one instead of using for+if:
rem  inside a for block, "if not defined PY" is expanded early and
rem  always sees the initial empty value, so the LAST successful
rem  candidate would overwrite the earlier ones. Sequential is safe.
set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY where py >nul 2>&1 && set "PY=py"
if not defined PY where python3 >nul 2>&1 && set "PY=python3"
if not defined PY (
  echo [ERROR] Python not found.
  echo.
  echo   This project needs Python 3.10 or newer.
  echo   Download: https://www.python.org/downloads/
  echo   IMPORTANT: tick "Add python.exe to PATH" during install.
  echo.
  echo   After installing, close this window and double-click
  echo   this file again.
  goto :end
)
rem  NOTE: inside the for /f command string use %PY% -- !PY! does not expand there.
for /f "delims=" %%v in ('%PY% --version 2^>^&1') do echo   Python      : %%v

rem ---- 2. Check / install backend dependencies ----
echo   Dependencies: checking...
rem  IMPORTANT: this probe list must cover EVERY package the app imports at
rem  runtime, not just the "core" web stack. It used to check only
rem  fastapi/uvicorn/sqlalchemy/jwt/pydantic_settings, so a machine that had
rem  those but was missing pandas was judged "all good" and the installer was
rem  skipped entirely -- the user then hit
rem    ModuleNotFoundError: No module named 'pandas'
rem  on Excel import, and re-running this script never fixed it because the
rem  probe kept passing. Adding pandas here is what actually unblocks them.
rem  When you add a dependency to backend\requirements.txt, add it here too.
!PY! -c "import fastapi, uvicorn, sqlalchemy, jwt, pydantic_settings, xlrd, openpyxl, httpx" >nul 2>&1
if errorlevel 1 (
  echo.
  echo   ------------------------------------------------------------
  echo   Backend dependencies are missing. Installing now...
  echo   This needs internet access and may take 1-3 minutes on the
  echo   first run. Please wait, do NOT close this window.
  echo   ------------------------------------------------------------
  echo.
  echo   [1/2] Trying Tsinghua mirror ^(fast in China^)...
  !PY! -m pip install --disable-pip-version-check -q -r "!ROOT!\backend\requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple
  if errorlevel 1 (
    echo.
    echo   Mirror failed. Trying the official PyPI source...
    !PY! -m pip install --disable-pip-version-check -q -r "!ROOT!\backend\requirements.txt"
  )
  echo.
  !PY! -c "import fastapi, uvicorn, sqlalchemy, jwt, pydantic_settings, xlrd, openpyxl, httpx" >nul 2>&1
  if errorlevel 1 (
    echo ------------------------------------------------------------
    echo [ERROR] Dependency installation failed.
    echo.
    echo   Please run this manually to see the full error:
    echo       cd /d "!ROOT!\backend"
    echo       !PY! -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    echo.
    echo   Common causes:
    echo     - No internet access
    echo     - Python is too new/old for a package
    echo ------------------------------------------------------------
    goto :end
  )
  echo   [2/2] Dependencies installed successfully.
  echo.
)
echo   Dependencies: OK

rem ---- 3. Pick free ports ----
set "APIPORT=8000"
set "WEBPORT=5500"

:portcheck
netstat -ano | findstr /r /c:"LISTENING" | findstr /c:":!APIPORT! " >nul 2>&1
if not errorlevel 1 (
  set /a APIPORT+=1
  if !APIPORT! GTR 8020 goto :portfail
  goto :portcheck
)

:portcheck2
netstat -ano | findstr /r /c:"LISTENING" | findstr /c:":!WEBPORT! " >nul 2>&1
if not errorlevel 1 (
  set /a WEBPORT+=1
  if !WEBPORT! GTR 5520 goto :portfail
  goto :portcheck2
)

echo   Ports       : API !APIPORT! / WEB !WEBPORT!
echo.

rem ---- 4. Generate helper scripts ----
rem  Each helper cds into its OWN folder via %%~dp0, so the
rem  working directory is always correct regardless of where the
rem  user launched this file from.
set "BACKEND_RUN=!ROOT!\backend\_run_backend.bat"
set "FRONTEND_RUN=!ROOT!\frontend\_run_frontend.bat"

> "!BACKEND_RUN!" echo @echo off
>>"!BACKEND_RUN!" echo title CampusAI-Backend
>>"!BACKEND_RUN!" echo cd /d "%%~dp0"
>>"!BACKEND_RUN!" echo echo.  
>>"!BACKEND_RUN!" echo echo  Backend starting on port !APIPORT! ...
>>"!BACKEND_RUN!" echo echo  Keep this window OPEN. Close it to stop the server.
>>"!BACKEND_RUN!" echo echo.
>>"!BACKEND_RUN!" echo set CAMPUS_API_PORT=!APIPORT!
rem --reload: auto-restart the backend when a .py file is saved, so you do not
rem have to close and reopen the window. Why it matters: without --reload, edits
rem to routers/planner.py silently keep running the OLD logic (vision parsing and
rem period parsing appear "broken" while it is really just a stale process).
>>"!BACKEND_RUN!" echo !PY! -m uvicorn main:app --host 0.0.0.0 --port !APIPORT! --reload

> "!FRONTEND_RUN!" echo @echo off
>>"!FRONTEND_RUN!" echo title CampusAI-Frontend
>>"!FRONTEND_RUN!" echo cd /d "%%~dp0"
>>"!FRONTEND_RUN!" echo echo.
>>"!FRONTEND_RUN!" echo echo  Frontend starting on port !WEBPORT! ...
>>"!FRONTEND_RUN!" echo echo  Keep this window OPEN. Close it to stop the server.
>>"!FRONTEND_RUN!" echo echo.
rem Use our own static server instead of `python -m http.server`.
rem WHY: plain http.server sends no Cache-Control at all, so the browser is free
rem to keep using a cached login.html / *.js. That produced a very confusing
rem symptom: the user replaced the package, deleted the old files AND closed the
rem old windows, yet the login page still showed an OLDER build tag -- it looked
rem like the update had failed, when actually the browser never asked the
rem server for the new page. (The ?v= cache-busting in the HTML cannot help,
rem because if the HTML itself is cached, the new ?v= is never read.)
rem serve_nocache.py sends no-store for .html/.js/.css and also refuses to
rem serve source files like .py/.env.
rem
rem !! DO NOT pass --dir "%%~dp0" here. Read this before "fixing" it !!
rem %%~dp0 expands to a path that ALREADY ENDS WITH A BACKSLASH, e.g.
rem     C:\Users\me\my-web\frontend\
rem so building  --dir "%%~dp0"  yields the literal text
rem     --dir "C:\Users\me\my-web\frontend\"
rem cmd treats backslash-quote as an ESCAPED quote, so the closing quote is
rem swallowed and the argument literally includes a trailing quote character.
rem Python then reports:
rem     "[ERROR] directory does not exist: C:\Users\me\my-web\frontend\""
rem (that stray quote at the end is the tell). The page never started.
rem We simply do not pass --dir at all: the line just above already does
rem     cd /d "%%~dp0"
rem and serve_nocache.py defaults to --dir "." (current directory), which is
rem exactly this folder. Fewer moving parts, no quoting trap.
if exist "!ROOT!\frontend\serve_nocache.py" (
  >>"!FRONTEND_RUN!" echo !PY! serve_nocache.py !WEBPORT! --bind 0.0.0.0
) else (
  rem Fallback: still works, just without the no-cache headers.
  >>"!FRONTEND_RUN!" echo !PY! -m http.server !WEBPORT! --bind 0.0.0.0
)

rem ---- 5. Start both services ----
echo [1/2] Starting BACKEND  on http://127.0.0.1:!APIPORT! ...
start "CampusAI-Backend" cmd /k "!BACKEND_RUN!"

echo       Waiting for the backend to be ready...
set /a WAIT=0
:waitapi
timeout /t 1 /nobreak >nul
set /a WAIT+=1
powershell -NoProfile -Command "try{(Invoke-WebRequest -Uri 'http://127.0.0.1:!APIPORT!/' -UseBasicParsing -TimeoutSec 2).StatusCode}catch{exit 1}" >nul 2>&1
if not errorlevel 1 goto :apiready
if !WAIT! LSS 25 goto :waitapi
echo       [WARN] Backend did not answer in !WAIT!s. Continuing anyway.
echo              Check the "CampusAI-Backend" window for errors.
goto :startweb

:apiready
echo       Backend is ready ^(took !WAIT!s^).

:startweb
echo [2/2] Starting FRONTEND on http://127.0.0.1:!WEBPORT! ...
start "CampusAI-Frontend" cmd /k "!FRONTEND_RUN!"
timeout /t 3 /nobreak >nul

rem ---- 6. Detect LAN IP for phone access ----
rem
rem !! WHY THIS IS SO CONVOLUTED - read before touching !!
rem
rem V1 just did `ipconfig | findstr "IPv4"` and took the FIRST match. On a machine
rem with VMware / VirtualBox / WSL / Hyper-V installed, a virtual adapter's IPv4
rem often comes first, so the script cheerfully printed something like 192.168.56.1
rem (VMware's host-only subnet). That address is only reachable from this PC --
rem the phone can never open it. The user sees "this page cannot reach the server"
rem on a phone that IS on the same hotspot, then goes off blaming the firewall
rem or the code. It was just the wrong NIC all along.
rem
rem FIX: classify each IPv4 by its adapter heading, then prefer Wi-Fi over Ethernet.
rem   1) Tag the IPv4 line that follows an adapter heading as WIFI or ETH.
rem   2) Drop loopback (127.), APIPA (169.254.) and the subnets virtual NICs use
rem      by default (192.168.56. / 192.168.99. / 172.16-172.3 = VMware/VBox/WSL).
rem   Wi-Fi wins: on campus/hotspot setups the phone is almost always on Wi-Fi,
rem   while an Ethernet cable is often unplugged or goes somewhere else entirely.
rem
rem NOTE: we deliberately do NOT match virtual adapters BY NAME. Those names vary
rem wildly across locales and vendors ("VMware Network Adapter VMnet1",
rem "vEthernet (WSL)", ...) and become an endless maintenance chore. Matching the
rem subnet is far more stable.
rem
rem HOW WE STAY ASCII-ONLY (important, do not "fix" this):
rem   Windows prints the heading in the system language, so on Chinese Windows it
rem   reads "<localized> WLAN" instead of "Wireless LAN adapter Wi-Fi". Matching
rem   that localized word would put non-ASCII bytes in this .bat and cmd would
rem   garble them. So instead of matching "the adapter word", we match the Wi-Fi
rem   markers "Wi-Fi" / "WLAN" / "Wireless" -- Windows prints those in Latin
rem   script on EVERY locale. Anything else that carries an IPv4 is Ethernet.
rem   Also set NIC_KIND's default to ETH so a heading we fail to recognize still
rem   lands somewhere useful rather than being dropped.
rem
rem !! This block must stay in sync with the same block in "..\start.bat" (the copy
rem !! under the launcher-tools folder).
rem
rem !! AND IT MUST STAY IN SYNC WITH tools\test_lan_ip.py, which unit-tests this
rem !! exact logic against simulated ipconfig output. Change one, change the other.
rem
rem TRAP 1: the findstr filter below decides which lines we ever see. If it only
rem   lets through "adapter" and "IPv4", then on Chinese Windows -- where the Wi-Fi
rem   heading reads "<cn> WLAN:" and contains NEITHER word -- the heading is dropped
rem   before we can classify it, Wi-Fi is never tagged, and "Wi-Fi first" silently
rem   stops working. So "WLAN" / "Wi-Fi" must be part of the filter too.
rem TRAP 2: do NOT classify by "line ends with a colon". Property lines such as
rem   "Connection-specific DNS Suffix  . :" also end with a colon and would
rem   overwrite the Wi-Fi tag we just set, before the IPv4 line arrives. Only the
rem   real heading sets NIC_KIND.
rem
rem TRAP 3 -- THE ONE THAT BIT US HARD (fixed, do not reintroduce):
rem   The original code did:
rem       for /f "tokens=1,* delims=:" %%a in (`ipconfig ^| findstr ...`) do (
rem         set "LINE=%%a %%b"
rem         ... then later ...
rem         for /f "tokens=2 delims=:" %%c in ("!LINE!") do set "CAND=%%c"
rem   The idea was: rebuild the line, then split it a second time to grab the IP.
rem   But `for /f` with delims=: CONSUMES the delimiter -- %%a and %%b come back
rem   WITHOUT the colon. So "LINE" was:
rem       '   IPv4 <suffix>   192.168.1.5'      <- no colon left!
rem   The second split then found no colon at all, %%c never got set, CAND stayed
rem   empty, LANIP stayed empty, and the script cheerfully opened
rem       http://:5500/login.html
rem   which the browser turned into "%20=:5500/login.html" and reported as
rem   NXDOMAIN. Net effect: NOTHING could be opened -- not on the PC, not on the
rem   phone. It LOOKED like a network problem but was pure string mangling.
rem
rem   Fix: grab the IP inside the SAME for /f that reads the line, using a nested
rem   for /f on the raw token stream (%%b still holds everything after the first
rem   colon). Do NOT rebuild the line and re-split it.
set "LANIP="
set "LANIP_WIFI="
set "LANIP_ETH="
set "NIC_KIND=ETH"
for /f "usebackq tokens=1,* delims=:" %%a in (`ipconfig ^| findstr /i /c:"adapter" /c:"IPv4" /c:"WLAN" /c:"Wi-Fi"`) do (
  rem !! %%b is everything AFTER the first colon. The colon itself is consumed by
  rem !! for /f -- that is exactly why we must read the IP from %%b here instead of
  rem !! rebuilding a string and splitting it again (see TRAP 3 above).
  set "LINE=%%a"
  rem --- Adapter heading? We only reach here for adapter headings, Wi-Fi markers
  rem     and IPv4 lines, so "not an IPv4 line" is a good enough test. ---
  echo !LINE! | findstr /i /c:"IPv4" >nul
  if errorlevel 1 (
    set "NIC_KIND=ETH"
    echo !LINE! | findstr /i /c:"Wi-Fi" /c:"WLAN" /c:"Wireless" >nul
    if not errorlevel 1 set "NIC_KIND=WIFI"
  )
  rem --- IPv4 line? The IP lives in %%b; strip spaces then validate. ---
  echo !LINE! | findstr /i /c:"IPv4" >nul
  if not errorlevel 1 (
    set "CAND=%%b"
    set "CAND=!CAND: =!"
    if not "!CAND!"=="" (
      rem loopback / APIPA: never useful for a phone
      echo !CAND! | findstr /b /c:"127." /c:"169.254." >nul
      if errorlevel 1 (
        rem default subnets of virtual NICs: never useful either
        echo !CAND! | findstr /b /c:"192.168.56." /c:"192.168.99." /c:"172.16." /c:"172.17." /c:"172.18." /c:"172.19." /c:"172.2" /c:"172.3" >nul
        if errorlevel 1 (
          if "!NIC_KIND!"=="WIFI" if not defined LANIP_WIFI set "LANIP_WIFI=!CAND!"
          if "!NIC_KIND!"=="ETH"  if not defined LANIP_ETH  set "LANIP_ETH=!CAND!"
        )
      )
    )
  )
)
rem Wi-Fi first, Ethernet as fallback
if defined LANIP_WIFI set "LANIP=!LANIP_WIFI!"
if not defined LANIP if defined LANIP_ETH set "LANIP=!LANIP_ETH!"
rem If both buckets came up empty (e.g. only virtual adapters exist), fall back to
rem the old behaviour: grab any non-loopback IPv4. Printing something the user can
rem recognise beats printing nothing and leaving them to guess.
if not defined LANIP (
  for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i /c:"IPv4"') do (
    if not defined LANIP (
      set "TMP=%%a"
      set "TMP=!TMP: =!"
      echo !TMP! | findstr /b /c:"127." >nul
      if errorlevel 1 if not "!TMP!"=="" set "LANIP=!TMP!"
    )
  )
)

echo.
echo ============================================================
echo   READY
echo ============================================================
echo   PC      : http://127.0.0.1:!WEBPORT!/login.html
echo   API docs: http://127.0.0.1:!APIPORT!/docs
echo   One-port: http://127.0.0.1:!APIPORT!/app/login.html
if defined LANIP (
  echo.
  echo   --- PHONE ^(connect to the SAME Wi-Fi^) ---
  echo   Recommended ^(one port, fewer moving parts^):
  echo            http://!LANIP!:!APIPORT!/app/login.html
  echo   Alternative ^(frontend port^):
  echo            http://!LANIP!:!WEBPORT!/login.html
  echo.
  echo   On the phone, open the link above and REGISTER / LOGIN there.
  echo   This PC's login page also shows a QR code ^(see "Scan login" tab^)
  echo   that you can scan with WeChat / QQ to open that page on your phone.
  echo.
  echo   Cannot open it on the phone? Run  check-lan.bat  for diagnostics.
)
echo ============================================================
echo.
echo   - Two windows were opened (backend / frontend).
echo     KEEP THEM OPEN while using the site.
echo   - Close those two windows to stop the servers.
echo.

rem ---- 7. LAN self-test ----
rem
rem Why this is worth the lines: when a phone cannot open the page, the user has
rem no way to tell apart
rem   (a) the script picked the wrong NIC
rem   (b) Windows Firewall is blocking inbound traffic
rem   (c) the phone is not on the same network
rem Fetching the LAN IP from this machine itself rules out (a) immediately:
rem if the local machine can reach it, the NIC choice and the bind are both fine,
rem so the remaining suspects are only the firewall or the phone.
rem
rem !! MUST test with LANIP, never with 127.0.0.1. When a server binds 0.0.0.0,
rem !! 127.0.0.1 always answers -- testing with it verifies nothing at all. That
rem !! is the easiest way to fool yourself into thinking LAN access works.
if defined LANIP (
  set "LANOK="
  for /f %%s in ('powershell -NoProfile -Command "try{(Invoke-WebRequest -Uri 'http://!LANIP!:!APIPORT!/api/ping' -UseBasicParsing -TimeoutSec 3).StatusCode}catch{exit 1}"') do set "LANOK=%%s"
  if not defined LANOK (
    echo [WARN] Self-test via !LANIP! FAILED.
    echo        Most likely the Windows Firewall is blocking inbound connections.
    echo        The phone will NOT be able to open the page until this is fixed.
    echo.
    echo        Option A ^(recommended^): run THIS in an ADMIN command prompt, once:
    echo            netsh advfirewall firewall add rule name="CampusAI API" dir=in action=allow protocol=TCP localport=!APIPORT!
    echo            netsh advfirewall firewall add rule name="CampusAI WEB" dir=in action=allow protocol=TCP localport=!WEBPORT!
    echo          ^(we do NOT do this automatically: it needs admin rights and would
    echo           pop a UAC prompt, which is worse than a copy-paste^)
    echo.
    echo        Option B: run  check-lan.bat  to see all detected adapters and IPs.
    echo                  If it picked a virtual adapter ^(VMware/VirtualBox/WSL^),
    echo                  just use the Wi-Fi IP it lists instead.
  ) else (
    echo [OK] LAN self-test passed: http://!LANIP!:!APIPORT!/api/ping
    echo.
    echo      CAREFUL: this test runs ON THIS PC, so it only proves the NIC choice
    echo      and the port binding are fine. It does NOT prove the PHONE can reach
    echo      us -- traffic from another device is a different path, and it is
    echo      blocked in two very common cases:
    echo        1^) Windows Firewall blocks inbound traffic for the python process.
    echo        2^) The Wi-Fi isolates clients from each other. Campus and guest
    echo           networks do this a lot: every device gets an IP, but devices
    echo           cannot talk to each other at all ^(no code change can fix this^).
    echo      So if the phone says "cannot open the page", it is one of those two
    echo      -- the QR code and the login logic are NOT the problem.
    echo.
    echo      Workaround that always works on an isolated network: turn ON the
    echo      phone's own hotspot and join it FROM this PC, then run this script
    echo      again ^(the phone is the router now, so it can reach us^).
    echo      Or skip scanning: log in with the account/password tab.
  )
)
echo.
rem ---- Open the browser on 127.0.0.1, NOT the LAN address ----
rem
rem !! WHY 127.0.0.1 - read before "fixing" this back !!
rem
rem 127.0.0.1 always resolves on this machine and never depends on which NIC the
rem LAN-IP helper happened to pick. If we opened http://LANIP:PORT and that helper
rem had picked a virtual adapter (VMware / VirtualBox / WSL), the browser would
rem sit on a page that never loads -- a confusing failure right at startup, and
rem one that has nothing to do with the app itself.
rem
rem THE QR CODE DOES NOT DEPEND ON THIS CHOICE.
rem   This used to be a real bug: the login page built its QR from location.origin,
rem   so opening 127.0.0.1 produced a QR pointing at 127.0.0.1 -- a phone scanning
rem   it dials ITSELF and can never load the page. That is fixed on the page now:
rem   it asks the backend (/api/system/lan-ip) for this machine's LAN IP and
rem   encodes THAT. If the LAN IP cannot be detected, the page falls back to the
rem   page address and says so in the scan pane, instead of showing a silently
rem   useless QR.
rem
rem NOTE: if the phone reports "cannot open the page", that is a NETWORK problem
rem   (firewall, or client isolation on the Wi-Fi), not something this URL choice
rem   affects. See the LAN self-test banner above, which spells out the workaround.
rem
rem The long "PHONE" banner below MUST keep printing the LAN address - that is what
rem users type on their phone. Keep the two consistent.
rem
rem !! This block must stay in sync with the same block in "..\start.bat" (the copy
rem !! under the launcher-tools folder). tools\sync_launcher.py enforces that.
echo Opening the browser at http://127.0.0.1:!WEBPORT!/login.html
echo   ^(the phone link is printed above; the QR code carries the LAN IP^)
start "" "http://127.0.0.1:!WEBPORT!/login.html"

goto :end

:portfail
echo [ERROR] Ports 8000-8020 / 5500-5520 are all in use.
echo         Close some programs and try again.

:end
echo.
echo ---- This window is paused so you can read the messages ----
pause
