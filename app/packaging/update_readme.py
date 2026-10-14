"""Replace documentation in a verified clean ZIP without reading a used folder."""
import argparse
from copy import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import zipfile

from release_gate import (private_path, credentials_in, KEY_PATTERN,
                          KEY_UTF16_LE, KEY_UTF16_BE)


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--readme', type=Path, default=Path(__file__).with_name('Readme.md'))
    args = parser.parse_args()
    archive = args.archive.resolve()
    baseline = digest(archive)
    if baseline != args.expected_sha256.lower():
        raise SystemExit('Input ZIP does not match the previously audited clean release.')
    key = os.environ.get('DEEPSEEK_API_KEY', '')
    if credentials_in(args.readme, [key]):
        raise SystemExit('README credential check failed.')
    needles = [key.encode(e) for e in ('utf-8', 'utf-16-le', 'utf-16-be')] if key else []
    overlap = max([512] + [len(n) for n in needles])
    temporary = archive.with_suffix('.zip.readme-update')
    copied = 0
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(
            temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        members = source.infolist()
        roots = {PurePosixPath(info.filename).parts[0] for info in members}
        if len(roots) != 1:
            raise RuntimeError('Unexpected release structure.')
        root = roots.pop()
        replaced = {root + '/使用说明.md', root + '/Readme.md'}
        for info in members:
            rel = PurePosixPath(info.filename)
            if rel.is_absolute() or '..' in rel.parts or private_path(rel):
                raise RuntimeError('Private or unsafe ZIP member: ' + info.filename)
            if info.filename in replaced:
                continue
            tail = b''
            updated = copy(info)
            updated.compress_type = zipfile.ZIP_DEFLATED
            with source.open(info) as incoming, target.open(updated, 'w') as outgoing:
                while block := incoming.read(1024 * 1024):
                    data = tail + block
                    if (KEY_PATTERN.search(data) or KEY_UTF16_LE.search(data) or
                            KEY_UTF16_BE.search(data) or any(n in data for n in needles)):
                        raise RuntimeError('Credential found in ZIP member: ' + info.filename)
                    tail = data[-overlap:]
                    outgoing.write(block)
            copied += 1
        target.write(args.readme, root + '/Readme.md')
    print('Checking new ZIP contents...', flush=True)
    with zipfile.ZipFile(temporary) as result:
        names = result.namelist()
        if result.testzip() is not None:
            raise RuntimeError('ZIP integrity check failed.')
        if root + '/使用说明.md' in names or root + '/Readme.md' not in names:
            raise RuntimeError('README replacement failed.')
        if result.read(root + '/Readme.md') != args.readme.read_bytes():
            raise RuntimeError('README content mismatch.')
    checksum = digest(temporary)
    report = {'files_scanned': copied + 1, 'findings': [],
              'input_sha256': baseline, 'output_sha256': checksum,
              'used_folder_read': False, 'zip_crc_verified': True,
              'documentation': 'Readme.md'}
    temporary.replace(archive)
    archive.with_suffix('.sha256').write_text(checksum + '  ' + archive.name + '\n', encoding='ascii')
    archive.with_suffix('.audit.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'archive': str(archive), 'bytes': archive.stat().st_size,
                      'audit': report}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
