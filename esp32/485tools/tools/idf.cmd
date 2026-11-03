@echo off
REM ============================================================
REM  ESP-IDF v6.1 headless build wrapper (EIM layout)
REM  Location: C:\work2026\CardioLite\tools\idf.cmd
REM  Usage:    idf.cmd -C <project_dir> build
REM            idf.cmd -C <project_dir> set-target esp32s3
REM  Note:     mirrors C:\Espressif\tools\Microsoft.v6.1.PowerShell_profile.ps1
REM            (kept in sync manually; re-check if IDF/tools are upgraded)
REM ============================================================
setlocal

REM --- clear MSYS/MinGW markers leaked from Git Bash, otherwise idf.py refuses to run ---
set "MSYSTEM="
set "MSYS="
set "MINGW_PREFIX="
set "TERM="

set "IDF_PATH=C:\esp\v6.1\esp-idf"
set "IDF_TOOLS_PATH=C:\Espressif\tools"
set "IDF_PYTHON_ENV_PATH=C:\Espressif\tools\python\v6.1\venv"
set "ESP_IDF_VERSION=6.1"
set "IDF_VERSION=6.1.0"
set "ESP_ROM_ELF_DIR=C:\Espressif\tools\esp-rom-elfs\20241011\"
set "OPENOCD_SCRIPTS=C:\Espressif\tools\openocd-esp32\v0.12.0-esp32-20260703\openocd-esp32\share\openocd\scripts"

set "TOOLS=C:\Espressif\tools"
set "PATH=%TOOLS%\ccache\4.12.1\ccache-4.12.1-windows-x86_64;%TOOLS%\cmake\4.0.3\bin;%TOOLS%\dfu-util\0.11\dfu-util-0.11-win64;%TOOLS%\esp-clang\esp-21.1.3_20260408\esp-clang\bin;%TOOLS%\esp-clangd\esp-21.1.3_20260408\esp-clangd\bin;%TOOLS%\esp-rom-elfs\20241011\;%TOOLS%\esp32ulp-elf\2.38_20240113\esp32ulp-elf\bin;%TOOLS%\idf-exe\1.0.3\;%TOOLS%\ninja\1.12.1\;%TOOLS%\openocd-esp32\v0.12.0-esp32-20260703\openocd-esp32\bin;%TOOLS%\riscv32-esp-elf-gdb\17.1_20260402\riscv32-esp-elf-gdb\bin;%TOOLS%\riscv32-esp-elf\esp-15.2.0_20251204\riscv32-esp-elf\bin;%TOOLS%\xtensa-esp-elf-gdb\17.1_20260402\xtensa-esp-elf-gdb\bin;%TOOLS%\xtensa-esp-elf\esp-15.2.0_20251204\xtensa-esp-elf\bin;%TOOLS%\python\v6.1\venv\Scripts;%PATH%"

python "%IDF_PATH%\tools\idf.py" %*
exit /b %ERRORLEVEL%
