@echo off
REM ============================================================================
REM  tools\_py.cmd  -  Run any project python script with the AGREED venv python.
REM ----------------------------------------------------------------------------
REM  Why: the machine has several python.exe (Store 3.9 stub, IDE 3.13, managed
REM  3.13). Only the managed venv below has numpy/scipy/matplotlib, and it is the
REM  only one the project tools were verified against. Typing a bare `xxx.py` in
REM  cmd.exe does NOT run it (cmd resolves via PATHEXT, and .PY is normally not in
REM  PATHEXT -> the shell silently falls back to the file association, which may
REM  be pythonw/IDLE and therefore produce NO console output at all).
REM
REM  Usage (from project root OR any directory):
REM      tools\_py.cmd tools\_t109_snap_check.py --hex "AA 55 03 4A 00 00 24 ..."
REM      _py _t109_snap_check.py --model
REM      _py tools\_fw_build.py
REM ============================================================================
setlocal
set "PY=C:\Users\luom\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERR] venv python not found:
    echo       %PY%
    echo       Check ~/.workbuddy/binaries/python/envs/default
    exit /b 9009
)
"%PY%" %*
exit /b %ERRORLEVEL%
