"""Reject personal files and recognizable credentials without printing secrets."""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re

PRIVATE_DIRS = {'.git', '.local', '.venv', '__pycache__', '.pytest_cache', 'test-results'}
KEY_PATTERN = re.compile(rb'sk-[A-Za-z0-9_-]{24,}')
KEY_UTF16_LE = re.compile(rb's\x00k\x00-\x00(?:[A-Za-z0-9_-]\x00){24,}')
KEY_UTF16_BE = re.compile(rb'\x00s\x00k\x00-(?:\x00[A-Za-z0-9_-]){24,}')


def private_path(path):
    path = Path(path)
    return (bool(set(path.parts) & PRIVATE_DIRS) or
            path.name == '.env' or path.name.startswith('.env.') or
            path.name.startswith('conversations.sqlite3') or
            path.name.endswith(('.log', '-wal', '-shm')))


def credentials_in(path, secrets=()):
    needles = [s.encode(encoding) for s in secrets if s and len(s) >= 8
               for encoding in ('utf-8', 'utf-16-le', 'utf-16-be')]
    tail = b''
    overlap = max([512] + [len(s) for s in needles])
    with Path(path).open('rb') as f:
        while block := f.read(1024 * 1024):
            data = tail + block
            if (KEY_PATTERN.search(data) or KEY_UTF16_LE.search(data) or
                    KEY_UTF16_BE.search(data) or any(n in data for n in needles)):
                return True
            tail = data[-overlap:]
    return False


def audit_tree(root):
    root = Path(root)
    secrets = [os.environ.get('DEEPSEEK_API_KEY', '')]
    files = [p for p in root.rglob('*') if p.is_file()]
    def inspect(p):
        rel = p.relative_to(root)
        if private_path(rel):
            return {'path': rel.as_posix(), 'reason': 'personal_file'}
        elif credentials_in(p, secrets):
            return {'path': rel.as_posix(), 'reason': 'credential_pattern'}
        return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        findings = [result for result in pool.map(inspect, files) if result]
    return {'files_scanned': len(files), 'findings': findings}
