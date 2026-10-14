"""Build a Windows release from explicit public inputs, never from user state."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor

from release_gate import audit_tree, private_path

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
VERSION = '0.1.2'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'releases')
    args = parser.parse_args()
    if sys.platform != 'win32':
        raise SystemExit('Build this package on Windows x64.')
    name = f'GenshinHub-{VERSION}-windows-x64'
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    stage = output / name
    if stage.exists() or (output / (name + '.zip')).exists():
        raise SystemExit('Output already exists. Use a new --output directory.')
    subprocess.run([sys.executable, '-m', 'PyInstaller', 'packaging/teyvat.spec',
                    '--noconfirm', '--distpath', 'build/native',
                    '--workpath', 'build/pyinstaller'], cwd=APP, check=True)
    shutil.copytree(APP / 'build/native/GenshinHub', stage)
    print('Copying and verifying frozen corpus...', flush=True)
    manifest = json.loads((APP / 'knowledge.lock.json').read_text(encoding='utf-8'))
    def copy_source(item):
        rel, record = item
        if private_path(rel):
            raise RuntimeError('Private path in corpus manifest: ' + rel)
        source = (ROOT / rel).resolve()
        if not source.is_relative_to(ROOT):
            raise RuntimeError('Invalid manifest path')
        target = stage / 'data' / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if target.stat().st_size != record['bytes'] or digest(target) != record['sha256']:
            raise RuntimeError('Frozen corpus mismatch: ' + rel)
    with ThreadPoolExecutor(max_workers=8) as pool:
        for _ in pool.map(copy_source, manifest['files'].items()):
            pass
    for filename in ('knowledge_index.py', 'local_links.py'):
        target = stage / 'data/99-脚本与日志/scripts' / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / '99-脚本与日志/scripts' / filename, target)

    # Rebuild the projection from public sources in a new, isolated directory.
    # Never copy app/.local, even though its retrieval cache normally has no chats.
    cache_state = APP / 'build' / ('cache-' + os.urandom(6).hex())
    env = os.environ.copy()
    env.pop('DEEPSEEK_API_KEY', None)
    env['TEYVAT_USER_DATA'] = str(cache_state)
    env['TEYVAT_CORPUS'] = str(stage / 'data')
    subprocess.run([sys.executable, '-c',
                    'from twwiki.knowledge import Knowledge; Knowledge()'],
                   cwd=APP, env=env, check=True)
    cache_target = stage / '_internal/retrieval'
    cache_target.mkdir()
    for db in (cache_state / 'retrieval').glob('*.sqlite3'):
        target = cache_target / db.name
        shutil.copyfile(db, target)
        target.with_suffix('.json').write_text(
            json.dumps({'sha256': digest(target)}), encoding='utf-8')

    launcher = ('@echo off\r\nchcp 65001 >nul\r\nsetlocal\r\n'
                'cd /d "%~dp0"\r\nif errorlevel 1 goto failed\r\n'
                'echo 正在启动原神百科，请稍候。\r\n'
                '"%~dp0GenshinHub.exe" %*\r\n'
                'if errorlevel 1 goto failed\r\nexit /b 0\r\n'
                ':failed\r\necho 启动失败，请保留上方错误信息。\r\npause\r\nexit /b 1\r\n')
    (stage / '启动原神百科.cmd').write_bytes(launcher.encode('utf-8'))
    shutil.copyfile(APP / 'packaging/Readme.md', stage / 'Readme.md')
    notices = stage / '第三方许可'
    notices.mkdir()
    for distribution in importlib.metadata.distributions():
        for file in distribution.files or []:
            if ('dist-info' in str(file) and
                    ('license' in file.name.lower() or 'copying' in file.name.lower())):
                src = Path(distribution.locate_file(file))
                if src.is_file():
                    target = notices / (distribution.metadata['Name'] + '-' + file.name)
                    if not target.exists():
                        shutil.copyfile(src, target)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.exists():
        shutil.copyfile(python_license, notices / 'Python-LICENSE.txt')

    finish(stage)


def finish(stage):
    """Audit and archive an assembled package; useful after interrupted compression."""
    stage = Path(stage).resolve()
    output, name = stage.parent, stage.name
    archive = output / (name + '.zip')
    if archive.exists():
        raise RuntimeError('Refusing to overwrite a completed ZIP.')
    # Freeze release assets before auditing/compressing. A concurrent source edit
    # must not quietly produce a package with an older README or interface.
    resources = [('Readme.md', APP / 'packaging/Readme.md')]
    resources += [('_internal/static/' + p.name, p) for p in (APP / 'static').iterdir() if p.is_file()]
    expected_resources = {}
    for rel, source in resources:
        expected = digest(source)
        if digest(stage / rel) != expected:
            raise RuntimeError('Release resource differs from source: ' + rel)
        expected_resources[rel] = expected
    manifest = json.loads((stage / '_internal/knowledge.lock.json').read_text(encoding='utf-8'))
    print('Auditing release for personal files and credentials...', flush=True)
    audit = audit_tree(stage)
    audit['product'] = '原神百科 / GenshinHub'
    audit['inputs'] = 'fresh executable and frozen corpus; no used release folder'
    (output / (name + '.audit.json')).write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
    if audit['findings']:
        raise RuntimeError('Release privacy check failed; see audit (paths only).')
    temporary = archive.with_suffix('.zip.partial')
    print('Privacy check passed; compressing release...', flush=True)
    files = sorted(p for p in stage.rglob('*') if p.is_file())
    def read_small(file):
        info = zipfile.ZipInfo.from_file(file, file.relative_to(output).as_posix())
        return file, info, file.read_bytes() if info.file_size < 1024 * 1024 else None
    # Bound prefetch memory, parallelize small-file reads, stream large databases.
    with ThreadPoolExecutor(max_workers=8) as pool, zipfile.ZipFile(
            temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for offset in range(0, len(files), 64):
            for file, info, data in pool.map(read_small, files[offset:offset + 64]):
                if data is None:
                    z.write(file, info.filename)
                else:
                    z.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
    if temporary.stat().st_size >= 2 * 1024**3:
        raise RuntimeError('Release asset exceeds GitHub 2 GiB limit.')
    with zipfile.ZipFile(temporary) as z:
        for rel, expected in expected_resources.items():
            if hashlib.sha256(z.read(name + '/' + rel)).hexdigest() != expected:
                raise RuntimeError('Archived resource changed during packaging: ' + rel)
        for rel, source in resources:
            if digest(source) != expected_resources[rel]:
                raise RuntimeError('Source resource changed during packaging: ' + rel)
    temporary.rename(archive)
    (output / (name + '.sha256')).write_text(
        digest(archive) + '  ' + archive.name + '\n', encoding='ascii')
    print(json.dumps({'archive': str(archive), 'bytes': archive.stat().st_size,
                      'corpus_files': len(manifest['files']), 'audit': audit},
                     ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
