#!/usr/bin/env bash
# ============================================================
#  ESP-IDF v6.1 build wrapper for Git Bash (headless, no MSys refusal)
#  usage:  ./tools/idf.sh -C C:/work2026/CardioLite/xxx build
#  why:    idf.py refuses to run when MSYSTEM is set; Git Bash sets it.
#          This wrapper clears MSYSTEM and hands a Windows-style PATH
#          to python so child tool processes resolve correctly.
# ============================================================
set -u

TOOLS='C:\Espressif\tools'
export IDF_PATH='C:\esp\v6.1\esp-idf'
export IDF_TOOLS_PATH="$TOOLS"
export IDF_PYTHON_ENV_PATH="$TOOLS\python\v6.1\venv"
export ESP_IDF_VERSION='6.1'
export IDF_VERSION='6.1.0'
export IDF_CCACHE_ENABLE='1'
export ESP_ROM_ELF_DIR="$TOOLS\esp-rom-elfs\20241011\\"
export OPENOCD_SCRIPTS="$TOOLS\openocd-esp32\v0.12.0-esp32-20260703\openocd-esp32\share\openocd\scripts"

export PATH="$TOOLS\\ccache\\4.12.1\\ccache-4.12.1-windows-x86_64;$TOOLS\\cmake\\4.0.3\\bin;$TOOLS\\dfu-util\\0.11\\dfu-util-0.11-win64;$TOOLS\\esp-clang\\esp-21.1.3_20260408\\esp-clang\\bin;$TOOLS\\esp-clangd\\esp-21.1.3_20260408\\esp-clangd\\bin;$TOOLS\\esp-rom-elfs\\20241011\\;$TOOLS\\esp32ulp-elf\\2.38_20240113\\esp32ulp-elf\\bin;$TOOLS\\idf-exe\\1.0.3\\;$TOOLS\\ninja\\1.12.1\\;$TOOLS\\openocd-esp32\\v0.12.0-esp32-20260703\\openocd-esp32\\bin;$TOOLS\\riscv32-esp-elf-gdb\\17.1_20260402\\riscv32-esp-elf-gdb\\bin;$TOOLS\\riscv32-esp-elf\\esp-15.2.0_20251204\\riscv32-esp-elf\\bin;$TOOLS\\xtensa-esp-elf-gdb\\17.1_20260402\\xtensa-esp-elf-gdb\\bin;$TOOLS\\xtensa-esp-elf\\esp-15.2.0_20251204\\xtensa-esp-elf\\bin;$TOOLS\\python\\v6.1\\venv\\Scripts;C:\\Windows\\system32;C:\\Windows"

PY="/c/Espressif/tools/python/v6.1/venv/Scripts/python.exe"

/usr/bin/env "$PY" 'C:\work2026\CardioLite\tools\_idf_py.py' "$@"
exit $?
