"""Open and gracefully close only our packaged desktop test process."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

exe = Path(sys.argv[1]).resolve()
state = Path(__file__).resolve().parents[1] / 'test-results/portable-window-state'
env = os.environ.copy()
for key in ('DEEPSEEK_API_KEY', 'PYTHONPATH', 'PYTHONHOME', 'TEYVAT_CORPUS'):
    env.pop(key, None)
env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
env['TEYVAT_USER_DATA'] = str(state)
env['PYTHON_KEYRING_BACKEND'] = 'keyring.backends.null.Keyring'
state.mkdir(parents=True, exist_ok=True)
with (state / 'host-output.txt').open('wb') as log:
    process = subprocess.Popen([str(exe)], env=env, cwd=state, stdout=log, stderr=log,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    user32 = ctypes.windll.user32
    found = []
    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(handle, unused):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if pid.value == process.pid:
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(handle, title, 256)
            if title.value == '原神百科' and user32.IsWindowVisible(handle):
                found.append(handle)
        return True
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and process.poll() is None:
            user32.EnumWindows(visit, 0)
            if found:
                break
            time.sleep(.2)
        assert found, 'No visible native window; inspect test host-output.txt'
        # Ensure the window remains alive after initial construction.
        time.sleep(3)
        assert process.poll() is None
        user32.PostMessageW(ctypes.c_void_p(found[0]), 0x10, 0, 0)
        process.wait(timeout=20)
        assert process.returncode == 0
        print(json.dumps({'visible_native_window': True, 'graceful_exit': True,
                          'python_removed_from_path': True, 'key_removed_from_env': True}))
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
