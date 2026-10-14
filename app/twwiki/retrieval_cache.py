"""Build an app-owned projection of the frozen index; never update the corpus DB."""
import json
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from .freeze import fingerprint
from .paths import APP, STATE

def prepare(knowledge,lock):
    filename='frozen-'+fingerprint(Path(lock))[:20]+'-v1.sqlite3'
    bundled=APP/'retrieval'/filename
    if bundled.is_file():
        metadata=json.loads(bundled.with_suffix('.json').read_text(encoding='utf-8'))
        if metadata.get('sha256')!=fingerprint(bundled):
            raise ValueError('随程序提供的检索缓存校验失败，请重新解压完整发布包。')
        return bundled
    cache=STATE/'retrieval';cache.mkdir(parents=True,exist_ok=True)
    destination=cache/filename
    if destination.exists():return destination
    temporary=cache/(uuid.uuid4().hex+'.sqlite3')
    engine=knowledge.engine
    with closing(knowledge.connect()) as source, closing(sqlite3.connect(temporary)) as db:
        source.backup(db)
        existing={p:h for p,h in db.execute('SELECT path,sha256 FROM documents')}
        # Existing indexed source files plus the current reading/research directories.
        selected={p for p in existing if p in knowledge.files}
        for p in knowledge.files:
            path=Path(p)
            if path.suffix not in ('.md','.txt'):continue
            if p.startswith(('10-','20-','30-','40-','50-','60-')) and not path.name.startswith(('00-','_wiki')):
                selected.add(p)
            if p.startswith(('70-索引与检索/研究整理/','70-索引与检索/长篇时间线/',
                             '70-索引与检索/人物志/人物档案/','70-索引与检索/人物志/势力编年史/',
                             '70-索引与检索/人物志/专题/')) and not path.name.startswith('00-'):
                selected.add(p)
        removed=set(existing)-selected
        changed={p for p in selected if existing.get(p)!=knowledge.files[p]['sha256']}
        for p in sorted(removed|changed):
            db.execute('DELETE FROM search WHERE rowid IN (SELECT id FROM chunks WHERE path=?)',(p,))
            db.execute('DELETE FROM chunks WHERE path=?',(p,))
            db.execute('DELETE FROM edges WHERE source=?',(p,))
            db.execute('DELETE FROM documents WHERE path=?',(p,))
        for p in sorted(changed):
            path=knowledge.verify_file(knowledge.root/p)
            text=path.read_text(encoding='utf-8-sig')
            title=re.sub(r'^\d+[- ]*','',path.stem)
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?)',
                       (p,title,knowledge.files[p]['sha256'],engine.kind(p,text),len(text),'[]'))
            for start,end,heading,body in engine.chunks(text):
                if len(body.strip())<16:continue
                row=db.execute('INSERT INTO chunks(path,start_line,end_line,heading,body) VALUES(?,?,?,?,?)',
                               (p,start,end,heading,body))
                db.execute('INSERT INTO search(rowid,title,heading,body) VALUES(?,?,?,?)',
                           (row.lastrowid,engine.grams(title),engine.grams(heading),engine.grams(body)))
            for link in engine.links(text):
                target=engine.target(path,link.url)
                if target and target.is_relative_to(knowledge.root):
                    rel=target.relative_to(knowledge.root).as_posix()
                    if rel in knowledge.files:
                        db.execute('INSERT OR IGNORE INTO edges VALUES(?,?,?)',(p,rel,link.label))
        for target, in db.execute('SELECT DISTINCT target FROM edges').fetchall():
            if target not in knowledge.files:db.execute('DELETE FROM edges WHERE target=?',(target,))
        report={'removed_stale_paths':len(removed),'refreshed_documents':len(changed),
                'documents':db.execute('SELECT COUNT(*) FROM documents').fetchone()[0],
                'source_index_unchanged':True,'integrity':db.execute('PRAGMA quick_check').fetchone()[0]}
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('app_projection',json.dumps(report)))
        db.commit()
    try:temporary.replace(destination)
    except PermissionError:
        if not destination.exists():raise
        temporary.unlink()
    return destination
