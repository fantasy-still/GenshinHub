"""Extract the shipped ZIP, then exercise its EXE without developer Python paths."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import queue
import subprocess
import threading
import time
import zipfile
import shutil
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    checksum = args.archive.with_suffix('.sha256').read_text().split()[0]
    with args.archive.open('rb') as f:
        assert hashlib.file_digest(f, 'sha256').hexdigest() == checksum
    with zipfile.ZipFile(args.archive) as z:
        def extract(info):
            rel = PurePosixPath(info.filename)
            assert not rel.is_absolute() and '..' not in rel.parts
            assert ':' not in info.filename and '\\' not in info.filename
            # A fresh directory plus ordinary file writes cannot introduce symlinks.
            # Check member names before creating their shared parents in parallel.
            path = destination.joinpath(*rel.parts)
            assert path.is_relative_to(destination)
            path.parent.mkdir(parents=True, exist_ok=True)
            # Reading each complete member also verifies its ZIP CRC.
            with z.open(info) as source, path.open('wb') as target:
                shutil.copyfileobj(source, target)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(extract, z.infolist()))
    folder = next(p for p in destination.iterdir() if p.is_dir())
    env = os.environ.copy()
    for key in ('DEEPSEEK_API_KEY', 'PYTHONHOME', 'PYTHONPATH', 'TEYVAT_CORPUS', 'TEYVAT_USER_DATA'):
        env.pop(key, None)
    env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
    env['LOCALAPPDATA'] = str(destination / 'fresh-user')
    # Simulate a fresh account without reading/deleting the user's saved key.
    env['PYTHON_KEYRING_BACKEND'] = 'keyring.backends.null.Keyring'
    env['PYTHONIOENCODING'] = 'utf-8'
    help_result = subprocess.run(
        f'cmd.exe /d /c ""{folder / "启动原神百科.cmd"}" --help"',
        cwd=destination, env=env, input=b'', capture_output=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert help_result.returncode == 0 and b'--browser' in help_result.stdout, (
        help_result.stdout + help_result.stderr).decode('utf-8', errors='replace')
    out = queue.Queue()
    process = subprocess.Popen([str(folder / 'GenshinHub.exe'), '--serve'],
                               cwd=destination, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    def collect():
        for line in iter(process.stdout.readline, b''):
            out.put(line.decode('utf-8', errors='replace').strip())
    threading.Thread(target=collect, daemon=True).start()
    messages = []
    report = {}
    try:
        deadline = time.monotonic() + 90
        url = None
        while time.monotonic() < deadline:
            try:
                line = out.get(timeout=1)
            except queue.Empty:
                if process.poll() is not None:
                    break
                continue
            if line.startswith('http://127.0.0.1:'):
                url = line
                break
            messages.append(line)
        assert url, 'Packaged startup failed: ' + '\n'.join(messages)
        parts = urlsplit(url)
        with httpx.Client(base_url=f'{parts.scheme}://{parts.netloc}',
                          headers={'x-app-token': parts.fragment.removeprefix('token=')},
                          timeout=30, trust_env=False) as client:
            status = client.get('/api/status').raise_for_status().json()
            assert status['has_key'] is False and status['key_saved'] is False
            assert status['knowledge']['files'] == 47202
            assert client.get('/api/chats').json() == []
            assert client.get('/api/memories').json() == []
            homepage = client.get('/').raise_for_status().text
            assert '<title>原神百科</title>' in homepage and 'GenshinHub' in homepage
            interface = client.get('/static/app.js').raise_for_status().text
            assert '原神百科对话-' in interface
            # Search endpoint requires q, matching the real UI request.
            search = client.get('/api/search', params={'q': '丑角'}).raise_for_status().json()
            assert search['results']
            chat = client.post('/api/chats', json={'mode': 'lore'}).raise_for_status().json()
            client.delete('/api/chats/' + chat['id']).raise_for_status()
            assert client.get('/api/chats').json() == []
        report = {'passed': True, 'fresh_start_has_key': False, 'chats': 0,
                  'locked_files': 47202, 'search_results': len(search['results']),
                  'python_on_path': False, 'python_env_removed': True,
                  'archive_sha256_verified': True, 'archive_crc_verified': True}
        (destination / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report), flush=True)
    finally:
        # Only the child created by this isolated verification is terminated.
        process.terminate()
        process.wait(timeout=15)


if __name__ == '__main__':
    main()
