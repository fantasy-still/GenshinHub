"""Create once / verify a corpus fingerprint, without writing into the corpus."""
import concurrent.futures, hashlib, json, sys
from .paths import ROOT, LOCK, DATA_DIRS

def fingerprint(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()

def inventory():
    files = [p for d in DATA_DIRS for p in (ROOT/d).rglob('*') if p.is_file()]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        hashes = list(pool.map(fingerprint, files))
    return {p.relative_to(ROOT).as_posix(): {'sha256': h, 'bytes':p.stat().st_size} for p,h in zip(files, hashes)}

def main():
    action = sys.argv[1] if len(sys.argv)>1 else 'verify'
    if action == 'create':
        if LOCK.exists(): raise SystemExit('锁定清单已经存在；拒绝覆盖。')
        data = {'schema':1, 'snapshot':'2026-10-13', 'files':inventory()}
        LOCK.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'locked_files':len(data['files'])}))
    elif action == 'verify':
        before = json.loads(LOCK.read_text(encoding='utf-8'))['files']; after = inventory()
        result = {'locked_files':len(before), 'changed':[p for p in before if p in after and before[p]!=after[p]],
                  'missing':sorted(before.keys()-after.keys()), 'added':sorted(after.keys()-before.keys())}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result['changed'] or result['missing'] or result['added']: raise SystemExit(1)
    else: raise SystemExit('用法：python -m twwiki.freeze create|verify')

if __name__=='__main__': main()
