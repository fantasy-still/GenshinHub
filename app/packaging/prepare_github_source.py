"""Prepare a small, explicit source upload folder for GitHub's web interface."""
import argparse
import json
from pathlib import Path
import shutil

from release_gate import audit_tree

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    destination = args.output.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    files = [ROOT / '.gitignore', ROOT / '.gitattributes', ROOT / '启动原神百科.cmd']
    files += [APP / p for p in ('pyproject.toml', 'requirements-lock.txt',
                                'knowledge.lock.json', 'AGENTS.md')]
    for folder in ('twwiki', 'static', 'packaging', 'tests'):
        files += [p for p in (APP / folder).iterdir()
                  if p.is_file() and p.suffix in ('.py', '.html', '.css', '.js', '.md', '.spec', '.txt')]
    files += [APP / 'docs/源码开发说明.md']
    files += [ROOT / '99-脚本与日志/scripts' / p for p in ('knowledge_index.py', 'local_links.py')]
    for source in files:
        target = destination / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    guide = (APP / 'packaging/Readme.md').read_text(encoding='utf-8')
    (destination / 'README.md').write_text(
        guide + '\n\n## 源码开发\n\n'
        '开发者请阅读[源码开发说明](app/docs/源码开发说明.md)。'
        '应用代码在app目录；完整资料随Windows成品Release提供。'
        '当前代码许可证待项目维护者指定，剧情资料及第三方依赖保留原有归属。\n',
        encoding='utf-8')
    (destination / 'app/README.md').write_text(
        '# GenshinHub 应用源码\n\n'
        '开发环境、资料恢复及构建见[源码开发说明](docs/源码开发说明.md)。\n\n'
        '普通使用者请下载Windows成品Release；使用流程见[用户Readme](packaging/Readme.md)。\n',
        encoding='utf-8')
    audit = audit_tree(destination)
    all_files = [p for p in destination.rglob('*') if p.is_file()]
    largest = max(p.stat().st_size for p in all_files)
    if audit['findings']:
        raise RuntimeError('Source privacy check failed: ' + json.dumps(audit, ensure_ascii=True))
    if len(all_files) > 100 or largest > 25 * 1024**2:
        raise RuntimeError('Source folder exceeds GitHub web upload limits.')
    report = audit | {'total_bytes': sum(p.stat().st_size for p in all_files),
                      'largest_file_bytes': largest, 'web_upload_compatible': True}
    (destination.parent / 'source-upload-audit.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'source_folder': str(destination), 'audit': report}, ensure_ascii=True))


if __name__ == '__main__':
    main()
