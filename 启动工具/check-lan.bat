@echo off
rem ============================================================================
rem  check-lan.bat -- LAN / phone-access diagnostics
rem ============================================================================
rem
rem WHY THIS EXISTS:
rem   When the phone shows "this page cannot reach the server", the user is stuck
rem   guessing. There are three very different causes and they all look identical
rem   from the phone:
rem     (a) the launcher picked the WRONG network adapter (virtual NIC etc.)
rem     (b) Windows Firewall is blocking inbound connections
rem     (c) the phone is not actually on the same network
rem   This script shows every adapter and every IPv4 with its kind, marks which
rem   one the launcher would pick, and tells the user plainly what to do next --
rem   instead of leaving them staring at a black console window.
rem
rem !! Keep this file pure ASCII: cmd garbles non-ASCII text. !!
rem !! The adapter-kind rules here must match start-windows.bat section 6 and
rem !! tools\test_lan_ip.py. Change one, change all three.

setlocal enabledelayedexpansion
title CampusAI - LAN check

echo ============================================================
echo   Campus AI -- LAN / phone access check
echo ============================================================
echo.

rem ---- 1. Show every adapter and its IPv4, with a classification --------------
echo [1] Network adapters detected by Windows:
echo.
echo     KIND    IPv4             ADAPTER
echo     ------  ---------------  ----------------------------------------

set "LANIP="
set "LANIP_WIFI="
set "LANIP_ETH="
set "NIC_KIND=ETH"
set "NIC_NAME="

rem Show the raw adapter headings plus the IPv4 lines, then classify.
rem
rem !! TRAP: do NOT rebuild the line as "%%a %%b" and split it again.
rem !! for /f with delims=: CONSUMES the colon, so the rebuilt string has no colon
rem !! left and a second `tokens=2 delims=:` yields an empty value. That is exactly
rem !! how the launcher once ended up opening http://:5500/login.html (browser
rem !! showed "%20=:5500/login.html", NXDOMAIN) and nothing could be opened at all.
rem !! Read the IP straight from %%b -- it already holds everything after the colon.
for /f "usebackq tokens=1,* delims=:" %%a in (`ipconfig ^| findstr /i /c:"adapter" /c:"IPv4" /c:"WLAN" /c:"Wi-Fi"`) do (
  set "LINE=%%a"

  rem --- heading line? (we only see headings, Wi-Fi markers, IPv4 lines) ---
  echo !LINE! | findstr /i /c:"IPv4" >nul
  if errorlevel 1 (
    set "NIC_KIND=ETH"
    echo !LINE! | findstr /i /c:"Wi-Fi" /c:"WLAN" /c:"Wireless" >nul
    if not errorlevel 1 set "NIC_KIND=WIFI"
    set "NIC_NAME=!LINE!"
  ) else (
    rem --- IPv4 line: print it with its kind and record candidates ---
    rem The address lives in %%b (see the TRAP note above).
    set "CAND=%%b"
    set "CAND=!CAND: =!"
    if not "!CAND!"=="" (
      set "USABLE=yes"
      set "REASON="

      echo !CAND! | findstr /b /c:"127." >nul
      if not errorlevel 1 (set "USABLE=no" & set "REASON=loopback")

      echo !CAND! | findstr /b /c:"169.254." >nul
      if not errorlevel 1 (set "USABLE=no" & set "REASON=APIPA / no DHCP")

      echo !CAND! | findstr /b /c:"192.168.56." /c:"192.168.99." /c:"172.16." /c:"172.17." /c:"172.18." /c:"172.19." /c:"172.2" /c:"172.3" >nul
      if not errorlevel 1 (set "USABLE=no" & set "REASON=virtual NIC subnet")

      if "!USABLE!"=="yes" (
        echo     !NIC_KIND!    !CAND!        !NIC_NAME!
        if "!NIC_KIND!"=="WIFI" if not defined LANIP_WIFI set "LANIP_WIFI=!CAND!"
        if "!NIC_KIND!"=="ETH"  if not defined LANIP_ETH  set "LANIP_ETH=!CAND!"
      ) else (
        echo     SKIP    !CAND!        !NIC_NAME!  ^(!REASON!^)
      )
    )
  )
)

rem Wi-Fi first, then Ethernet (same rule as the launcher)
if defined LANIP_WIFI set "LANIP=!LANIP_WIFI!"
if not defined LANIP if defined LANIP_ETH set "LANIP=!LANIP_ETH!"

echo.
echo ============================================================
if defined LANIP (
  echo   WOULD USE : !LANIP!
) else (
  echo   WOULD USE : ^(nothing usable found^)
)
echo ============================================================
echo.

rem ---- 2. Test whether that IP is actually reachable from this PC ------------
if not defined LANIP (
  echo [2] Skipped: no usable LAN IP was found.
  echo     Most likely this PC is not connected to any network right now,
  echo     or it only has virtual adapters ^(VMware / VirtualBox / WSL^).
  echo     Connect to Wi-Fi and run this script again.
  goto :advice
)

echo [2] Self-test: can THIS PC reach http://!LANIP!:8000/api/ping ?
set "LANOK="
for /f %%s in ('powershell -NoProfile -Command "try{(Invoke-WebRequest -Uri 'http://!LANIP!:8000/api/ping' -UseBasicParsing -TimeoutSec 3).StatusCode}catch{exit 1}"') do set "LANOK=%%s"

if defined LANOK (
  echo     OK ^(!LANOK!^) -- the address works from this PC.
  echo     The backend is listening correctly on all adapters.
) else (
  echo     FAILED -- this PC cannot even reach that address.
  echo     Either the backend is not running, or the Firewall blocks it.
)

:advice
echo.
echo ============================================================
echo   WHAT TO DO NEXT
echo ============================================================
echo.
echo   1. Make sure the server is running ^(start-windows.bat^) and that you
echo      keep its two windows OPEN.
echo.
echo   2. On the phone: connect to the SAME Wi-Fi as this PC, then open
if defined LANIP (
  echo            http://!LANIP!:8000/app/login.html
) else (
  echo            http://^<the-ip-shown-above^>:8000/app/login.html
)
echo.
echo   3. Still failing? Run this in an ADMIN command prompt ^(once^):
echo            netsh advfirewall firewall add rule name="CampusAI 8000" dir=in action=allow protocol=TCP localport=8000
echo            netsh advfirewall firewall add rule name="CampusAI 5500" dir=in action=allow protocol=TCP localport=5500
echo.
echo   4. A phone hotspot note: the PHONE must share its network, and this PC
echo      must JOIN the hotspot. If this PC is the one sharing, the phone can
echo      usually not reach it -- use a normal Wi-Fi router instead.
echo.
echo   5. Note: when the launcher had to switch ports ^(8000 was busy^), the
echo      number above may differ. Check the launcher window for the real one.
echo.
pause
