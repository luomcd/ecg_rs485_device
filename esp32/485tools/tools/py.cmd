@echo off
REM ============================================================================
REM  py.cmd -- single Python entry point for CardioLite tools (2026-10-29)
REM
REM  Why this exists: on this machine `python` resolves to at least 3 binaries,
REM  and WHICH ONE CHANGES WITH THE SHELL:
REM     C:\Espressif\tools\python\python.exe                        <- after export.ps1
REM     %USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe  <- bare PS
REM     %USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe
REM  Their site-packages are ISOLATED, so a script works in one shell and dies
REM  with "no module named pyserial" in another.
REM
REM  This wrapper always uses the WorkBuddy env (has pyserial / numpy / scipy),
REM  independent of PATH.
REM
REM  Usage (either):
REM     cd C:\work2026\CardioLite\tools
REM     py s3_replay_capture.py --rec 105 --speed 8
REM
REM     cd C:\work2026\CardioLite
REM     tools\py tools\s3_replay_capture.py --rec 105 --speed 8
REM
REM  NOTE: keep this file ASCII-only + CRLF. cmd.exe renders UTF-8 Chinese as
REM  garbage under the default GBK code page.
REM ============================================================================
setlocal
REM  Encoding: these tools print UTF-8 (incl. symbols like the warning sign), which
REM  the GBK code page cannot encode at all -> UnicodeEncodeError. So pin UTF-8 here
REM  and switch the console to code page 65001 for this run.
REM  NOTE: this only fixes what you SEE on screen. If you capture the output with
REM  $out = tools\py ... 2>&1 , set [Console]::OutputEncoding = UTF8 first,
REM  otherwise the shell decodes it as gb2312 and you get mojibake.
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if exist "%PY%" goto run

echo [py.cmd] WARNING: WorkBuddy python not found:
echo           %PY%
echo [py.cmd] Falling back to PATH python -- if it complains about a missing
echo [py.cmd] module (pyserial/numpy), this is why.
set "PY=python"

:run
"%PY%" %*
exit /b %ERRORLEVEL%
