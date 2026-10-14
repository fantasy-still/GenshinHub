"""Local, source-aware Chinese lore retrieval. Standard-library Python only."""
from __future__ import annotations
import argparse, concurrent.futures, hashlib, json, re, sqlite3, sys, time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / '70-索引与检索/知识库'
DB = INDEX / 'knowledge.sqlite3'
RESEARCH = '70-索引与检索/研究整理/'
sys.path.insert(0, str(Path(__file__).parent))
from local_links import links, target

def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))

def aliases():
    p = INDEX / '实体别名.json'
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else []

def grams(text):
    """CJK bigrams permit two-character names; ASCII words remain searchable."""
    out=[]
    for s in re.findall(r'[\u3400-\u9fff]+|[a-zA-Z0-9]+', text.lower()):
        out.extend(s[i:i+2] for i in range(len(s)-1)) if len(s)>1 and '\u3400'<=s[0]<='\u9fff' else out.append(s)
    return ' '.join(out)

def candidates():
    for directory in ('10-剧情','20-世界观与设定','30-人物档案','40-文献与书籍','50-图鉴资料','60-活动与版本'):
        yield from (p for p in (ROOT/directory).rglob('*') if p.is_file() and p.suffix in {'.md','.txt'} and not p.name.startswith(('00-','_wiki')))
    for directory in ('70-索引与检索/研究整理','70-索引与检索/长篇时间线','70-索引与检索/人物志/势力编年史','70-索引与检索/人物志/专题'):
        yield from (p for p in (ROOT/directory).rglob('*.md') if not p.name.startswith('00-'))
    # Historical biographies are edited source drafts; avoid their auto-excerpt copies.
    bio=ROOT/'99-脚本与日志/人物志-生平'
    if bio.exists():
        yield from (p for p in bio.rglob('*.md') if not any(x.startswith(('22-','25-')) for x in p.relative_to(bio).parts))

def kind(path, text):
    if path.startswith(RESEARCH): return 'curated'
    if '/' not in path or path.startswith(('70-','99-')): return 'legacy_summary'
    if path.startswith('20-') or path.endswith('/提瓦特编年史.txt'): return 'wiki_secondary'
    if path.startswith('10-') and ('· ' in text or '对话内容' in text): return 'quest_transcript'
    if path.startswith('30-'): return 'character_archive_mixed'
    if path.startswith('40-') and re.search(r'卷\d+内容:', text): return 'in_game_book'
    if path.startswith('50-'): return 'item_archive_mixed'
    return 'archive_mixed'

def read_document(p):
    data=p.read_bytes()
    text=data.decode('utf-8-sig', errors='replace')
    rel=p.relative_to(ROOT).as_posix()
    return p, rel, text, hashlib.sha256(data).hexdigest(), kind(rel,text)

def chunks(text, limit=1100, overlap=160):
    """Keep exact line locations, even when a source stores a story on one long line."""
    heading=[]; buf=[]; length=0
    def render(items): return '\n'.join(t for _,t in items)
    for line_no,line in enumerate(text.splitlines(),1):
        match=re.match(r'^(#{1,6})\s+(.+)',line)
        if match and buf:
            yield buf[0][0],buf[-1][0], ' / '.join(heading),render(buf)
            buf=[]; length=0
        if match:
            level=len(match[1]); heading=heading[:level-1]+[match[2]]
        pieces=[line[i:i+limit] for i in range(0,len(line),limit-overlap)] if len(line)>limit else [line]
        for piece in pieces:
            if buf and length+len(piece)>limit:
                yield buf[0][0],buf[-1][0], ' / '.join(heading),render(buf)
                tail=[]; size=0
                for old in reversed(buf):
                    if size+len(old[1])>overlap: break
                    tail.insert(0,old); size+=len(old[1])+1
                buf=tail; length=size
            buf.append((line_no,piece)); length+=len(piece)+1
    if buf: yield buf[0][0],buf[-1][0], ' / '.join(heading),render(buf)

def connect(path=None):
    con=sqlite3.connect(DB if path is None else path); con.row_factory=sqlite3.Row
    return con

def build():
    INDEX.mkdir(parents=True,exist_ok=True)
    con=connect()
    con.executescript('''
    CREATE TABLE IF NOT EXISTS documents(path TEXT PRIMARY KEY,title TEXT,sha256 TEXT,kind TEXT,characters INTEGER,metadata TEXT);
    CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY,path TEXT,start_line INTEGER,end_line INTEGER,heading TEXT,body TEXT);
    CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
    CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(title, heading, body);
    CREATE TABLE IF NOT EXISTS edges(source TEXT,target TEXT,label TEXT,PRIMARY KEY(source,target,label));
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
    ''')
    metadata={}
    for line in (ROOT/'70-索引与检索/pages.jsonl').read_text(encoding='utf-8-sig').splitlines():
        rec=json.loads(line); metadata.setdefault(rec.get('file',''),[]).append({k:rec.get(k) for k in ('title','raw','revid','timestamp','categories')})
    existing={r['path']:(r['sha256'],r['kind']) for r in con.execute('SELECT path,sha256,kind FROM documents')}
    seen=set(); changed=0; counts={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for p,rel,text,sha,source_kind in pool.map(read_document, sorted(set(candidates()))):
            seen.add(rel); counts[source_kind]=counts.get(source_kind,0)+1
            if existing.get(rel)==(sha,source_kind): continue
            old_ids=[r[0] for r in con.execute('SELECT id FROM chunks WHERE path=?',(rel,))]
            con.executemany('DELETE FROM search WHERE rowid=?', [(i,) for i in old_ids])
            con.execute('DELETE FROM chunks WHERE path=?',(rel,)); con.execute('DELETE FROM edges WHERE source=?',(rel,))
            title=re.sub(r'^\d+[- ]*','',p.stem)
            con.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?,?)',(rel,title,sha,source_kind,len(text),json.dumps(metadata.get(rel,[]),ensure_ascii=False)))
            for start,end,heading,body in chunks(text):
                if len(body.strip())<16: continue
                cur=con.execute('INSERT INTO chunks(path,start_line,end_line,heading,body) VALUES(?,?,?,?,?)',(rel,start,end,heading,body))
                con.execute('INSERT INTO search(rowid,title,heading,body) VALUES(?,?,?,?)',(cur.lastrowid,grams(title),grams(heading),grams(body)))
            for link in links(text):
                dest=target(p,link.url)
                if dest and dest.is_relative_to(ROOT):
                    con.execute('INSERT OR IGNORE INTO edges VALUES(?,?,?)',(rel,dest.relative_to(ROOT).as_posix(),link.label))
            changed+=1
            if changed%500==0: con.commit()
    removed=set(existing)-seen
    for rel in removed:
        for row in con.execute('SELECT id FROM chunks WHERE path=?',(rel,)).fetchall(): con.execute('DELETE FROM search WHERE rowid=?',(row[0],))
        con.execute('DELETE FROM chunks WHERE path=?',(rel,)); con.execute('DELETE FROM documents WHERE path=?',(rel,)); con.execute('DELETE FROM edges WHERE source=?',(rel,))
    con.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('built_at',time.strftime('%Y-%m-%dT%H:%M:%S')))
    con.commit()
    result={'documents':len(seen),'changed_documents':changed,'removed_documents':len(removed),'chunks':con.execute('SELECT COUNT(*) FROM chunks').fetchone()[0],
      'source_links':con.execute('SELECT COUNT(*) FROM edges').fetchone()[0],'source_kinds':counts,'integrity':con.execute('PRAGMA quick_check').fetchone()[0],
      'scope':'本地已入库文本；2026-10-09增量隔离目录未合并；自动原文命中副本、原始wikitext、攻略不重复建立问答索引',
      'indexed_paths_sha256':hashlib.sha256('\n'.join(sorted(seen)).encode()).hexdigest()}
    (INDEX/'索引状态.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    con.close(); emit(result)

def expand(query):
    terms=[t for t in re.split(r'\s+',query.strip()) if t]
    expansions=[]
    for group in aliases():
        names=[group['name']]+group.get('aliases',[])+group.get('related_terms',[])
        if any((n in query if len(n)>1 else n in terms) for n in names):
            expansions.extend(names)
    return list(dict.fromkeys(terms+expansions)),expansions

def search(args):
    if not DB.exists(): raise SystemExit('索引尚未建立，请先运行 build。')
    con=connect(); terms,expanded=expand(args.query)
    tokens=list(dict.fromkeys(grams(' '.join(terms)).split()))[:96]
    if not tokens: raise SystemExit('请输入至少一个名字、事件名或关键词。')
    expression=' OR '.join('"'+x.replace('"','""')+'"' for x in tokens)
    sql='''SELECT c.*,d.title,d.kind,d.sha256,d.metadata,bm25(search,5,3,1) AS rank
      FROM search JOIN chunks c ON c.id=search.rowid JOIN documents d ON d.path=c.path WHERE search MATCH ?'''
    values=[expression]
    if args.layer=='summary': sql+=" AND d.kind IN ('curated','legacy_summary')"
    elif args.layer=='source': sql+=" AND d.kind NOT IN ('curated','legacy_summary','wiki_secondary')"
    if args.path: sql+=' AND d.path LIKE ?'; values.append('%'+args.path+'%')
    sql+=' ORDER BY rank LIMIT 250'
    rows=con.execute(sql,values).fetchall()
    stopwords={'为什么','为何','什么','如何','怎么','是不是','是否','关系','原因','请问','的','和','与','是'}
    original=[t for t in re.split(r'\s+',args.query.strip()) if t and t not in stopwords]
    def score(r):
        content=r['title']+' '+r['heading']+' '+r['body']
        coverage=sum(1 for t in terms if t in content)
        exact=sum(2 for t in terms if t in r['title'] or t in r['heading'])
        preferred=1.4 if r['kind']=='curated' else 1
        requested=sum(t in content for t in original)/max(1,len(original))
        # An extra query term such as “百年” must outweigh optional alias expansion.
        precision=(0.15+0.85*requested)**3 if len(original)>1 else 1
        return (-r['rank'])*(1+0.3*coverage+0.25*exact)*preferred*precision
    output=[]; per_path={}
    for row in sorted(rows,key=score,reverse=True):
        if per_path.get(row['path'],0)>=args.per_file: continue
        body=row['body']
        positions={0}
        for term in terms:
            for match in list(re.finditer(re.escape(term),body))[:12]: positions.add(max(0,match.start()-100))
        def window_score(pos):
            snippet=body[pos:pos+args.snippet]
            return sum((3 if term in original else 0.3)/max(1,body.count(term)) for term in terms if term in snippet)
        start=max(sorted(positions),key=window_score)
        provenance=json.loads(row['metadata'])
        output.append({'path':row['path'],'absolute_path':str(ROOT/row['path']),'lines':[row['start_line'],row['end_line']],
          'title':row['title'],'heading':row['heading'],'source_kind':row['kind'],'score':round(score(row),4),
          'snippet':body[start:start+args.snippet], 'sha256':row['sha256'],
          'source_metadata':[dict(record, raw_path=('80-原始归档/'+record['raw']) if record.get('raw') else None,
             wiki_url='https://wiki.biligame.com/ys/'+quote(record.get('title') or '')) for record in provenance[:3]],
          'source_metadata_count':len(provenance)})
        per_path[row['path']]=per_path.get(row['path'],0)+1
        if len(output)>=args.limit: break
    con.close()
    correction_path=INDEX/'已校订结论.json'
    corrections=json.loads(correction_path.read_text(encoding='utf-8')) if correction_path.exists() else []
    relevant=[c for c in corrections if any(term in args.query for term in c['match_any'])]
    emit({'query':args.query,'expanded_names':list(dict.fromkeys(expanded)),'layer':args.layer,'known_corrections':relevant,'results':output,
          'notice':'检索命中仅用于定位；来源类别不等于真实性评分。回答前读取上下文并回查原文，当前检索不证明资料齐全。'})

def read(args):
    p=(ROOT/args.path).resolve()
    if not p.is_relative_to(ROOT) or not p.is_file(): raise SystemExit('路径必须指向库内存在的文件。')
    lines=p.read_text(encoding='utf-8-sig').splitlines()
    first=max(1,args.start); last=min(len(lines),first+args.count-1)
    emit({'path':p.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
          'lines':[{'line':i,'text':lines[i-1]} for i in range(first,last+1)]})

def related(args):
    con=connect(); rel=args.path.replace('\\','/')
    result={'path':rel,'outbound_sources':[dict(r) for r in con.execute('SELECT target,label FROM edges WHERE source=?',(rel,))],
          'referenced_by':[dict(r) for r in con.execute('SELECT source,label FROM edges WHERE target=?',(rel,))]}
    con.close(); emit(result)

def check():
    con=connect(); problems=[]
    rows=con.execute('SELECT path,sha256 FROM documents').fetchall()
    def inspect(row):
        p=ROOT/row['path']
        if not p.exists(): return {'path':row['path'],'problem':'missing'}
        if hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']: return {'path':row['path'],'problem':'changed'}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool: problems=[x for x in pool.map(inspect,rows) if x]
    unindexed=sorted({p.relative_to(ROOT).as_posix() for p in candidates()}-{r['path'] for r in rows})
    integrity=con.execute('PRAGMA quick_check').fetchone()[0]
    con.close()
    emit({'documents':len(rows),'stale_or_missing':problems,'unindexed':unindexed,'integrity':integrity})
    if problems or unindexed or integrity!='ok': raise SystemExit(1)

def main():
    parser=argparse.ArgumentParser(description=__doc__); sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('build'); sub.add_parser('check')
    s=sub.add_parser('search'); s.add_argument('query'); s.add_argument('--layer',choices=['all','summary','source'],default='all'); s.add_argument('--path'); s.add_argument('--limit',type=int,default=8); s.add_argument('--per-file',type=int,default=2); s.add_argument('--snippet',type=int,default=600)
    r=sub.add_parser('read'); r.add_argument('path'); r.add_argument('--start',type=int,default=1); r.add_argument('--count',type=int,default=60)
    n=sub.add_parser('related'); n.add_argument('path')
    args=parser.parse_args()
    if hasattr(args,'limit') and not 1<=args.limit<=50: parser.error('--limit must be 1..50')
    if hasattr(args,'count') and not 1<=args.count<=300: parser.error('--count must be 1..300')
    {'build':build,'check':check,'search':lambda:search(args),'read':lambda:read(args),'related':lambda:related(args)}[args.action]()

if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    main()
