"""Exercise the shipped Windows entry point, including its working directory."""
import os
from pathlib import Path
import subprocess

import pytest

LAUNCHER = Path(__file__).resolve().parents[2] / '启动原神百科.cmd'


def test_cmd_uses_windows_line_endings():
    content = LAUNCHER.read_bytes()
    assert not content.startswith(b'\xef\xbb\xbf')
    assert b'\r\n' in content
    assert b'\n' not in content.replace(b'\r\n', b'')


@pytest.mark.skipif(os.name != 'nt', reason='Windows launcher')
def test_cmd_runs_from_unrelated_directory(tmp_path):
    env = os.environ.copy()
    env['TEYVAT_USER_DATA'] = str(tmp_path / 'isolated-state')
    env['PYTHONIOENCODING'] = 'utf-8'
    env.pop('DEEPSEEK_API_KEY', None)
    result = subprocess.run(
        f'cmd.exe /d /c ""{LAUNCHER}" --help"',
        cwd=tmp_path, env=env, input=b'', capture_output=True, timeout=30,
    )
    output = result.stdout.decode('utf-8', errors='replace')
    assert result.returncode == 0, output + result.stderr.decode('utf-8', errors='replace')
    assert '--browser' in output and '--serve' in output
    assert not result.stderr, result.stderr.decode('utf-8', errors='replace')
