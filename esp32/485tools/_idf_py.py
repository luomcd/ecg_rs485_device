"""Launch ESP-IDF's idf.py after scrubbing MSys markers.

Git Bash (MSYS2 runtime) re-injects MSYSTEM into the Windows environment block
of every child process, so `env -u MSYSTEM` does not help. ESP-IDF v6.1 checks
`'MSYSTEM' in os.environ` in idf.py's `__main__` block and, when found, prints a
warning and *skips* main() entirely -- which looks like a silent no-op build.

Usage:  python _idf_py.py <idf.py args...>
"""
import os
import runpy
import sys

for _k in ('MSYSTEM', 'MSYS', 'MINGW_PREFIX', 'MINGW_CHOST'):
    os.environ.pop(_k, None)

IDF_PY = r'C:\esp\v6.1\esp-idf\tools\idf.py'

sys.argv = [IDF_PY] + sys.argv[1:]
sys.path.insert(0, os.path.dirname(IDF_PY))
runpy.run_path(IDF_PY, run_name='__main__')
