import importlib.util
from pathlib import Path
import runpy
import sys
import json
import pytest

PACKAGING = Path(__file__).resolve().parents[1] / 'packaging'
spec = importlib.util.spec_from_file_location('release_gate', PACKAGING / 'release_gate.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def test_release_rejects_secrets_and_personal_files(tmp_path, monkeypatch):
    secret = 'sk-' + 'a' * 32
    (tmp_path / 'leak.txt').write_text('x' * (1024 * 1024 - 5) + secret)
    (tmp_path / '.env').write_text('SETTING=1')
    (tmp_path / 'conversations.sqlite3').touch()
    (tmp_path / 'wide.txt').write_text(secret, encoding='utf-16')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'opaque-test-credential-for-audit')
    (tmp_path / 'opaque.txt').write_text('opaque-test-credential-for-audit', encoding='utf-16-be')
    audit = gate.audit_tree(tmp_path)
    assert {f['path'] for f in audit['findings']} == {'.env', 'leak.txt', 'wide.txt', 'opaque.txt', 'conversations.sqlite3'}
    assert secret not in str(audit)


def test_frozen_paths_do_not_reuse_developer_data(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'download/TeyvatArchive.exe'))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'user'))
    monkeypatch.delenv('TEYVAT_CORPUS', raising=False)
    monkeypatch.delenv('TEYVAT_USER_DATA', raising=False)
    paths = runpy.run_path(str(PACKAGING.parent / 'twwiki/paths.py'))
    assert paths['ROOT'] == (tmp_path / 'download/data').resolve()
    assert paths['STATE'] == (tmp_path / 'user/TeyvatArchive').resolve()


def test_bundled_cache_is_verified_and_does_not_create_user_cache(tmp_path, monkeypatch):
    from twwiki import retrieval_cache as cache
    lock = tmp_path / 'lock.json'
    lock.write_text('{}')
    monkeypatch.setattr(cache, 'APP', tmp_path / 'bundle')
    monkeypatch.setattr(cache, 'STATE', tmp_path / 'user')
    bundled = cache.APP / 'retrieval' / ('frozen-' + cache.fingerprint(lock)[:20] + '-v1.sqlite3')
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b'public-test-projection')
    bundled.with_suffix('.json').write_text(json.dumps({'sha256': cache.fingerprint(bundled)}))
    assert cache.prepare(None, lock) == bundled
    assert not cache.STATE.exists()
    bundled.write_bytes(b'changed')
    with pytest.raises(ValueError, match='校验失败'):
        cache.prepare(None, lock)


def test_packaging_rejects_stale_interface_assets(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(PACKAGING))
    import build_release
    source = tmp_path / 'source'
    stage = tmp_path / 'release'
    (source / 'packaging').mkdir(parents=True)
    (source / 'static').mkdir()
    (stage / '_internal/static').mkdir(parents=True)
    (source / 'packaging/Readme.md').write_text('Public guide')
    (stage / 'Readme.md').write_text('Public guide')
    (source / 'static/app.js').write_text('current public interface')
    (stage / '_internal/static/app.js').write_text('stale public interface')
    monkeypatch.setattr(build_release, 'APP', source)
    with pytest.raises(RuntimeError, match='resource differs from source'):
        build_release.finish(stage)
    assert not stage.with_suffix('.zip').exists()
