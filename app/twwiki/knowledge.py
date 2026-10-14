"""Read-only retrieval adapter. No collector, build or filesystem-write tool exposed."""
import hashlib, importlib.util, json, sqlite3, sys, threading
from pathlib import Path
from urllib.parse import quote
from .paths import ROOT, LOCK
from .freeze import fingerprint

class Knowledge:
    def __init__(self, root=ROOT, lock=LOCK):
        self.root=Path(root).resolve(); self.files=json.loads(Path(lock).read_text(encoding='utf-8'))['files']
        self.references={'s/'+hashlib.sha256(p.encode()).hexdigest()[:20]:p for p in self.files}
        self.dbpath=self.root/'70-索引与检索/知识库/knowledge.sqlite3'
        self.verify_file(self.dbpath); self.guard=threading.Lock()
        script=self.root/'99-脚本与日志/scripts/knowledge_index.py'
        sys.path.insert(0,str(script.parent)); sys.dont_write_bytecode=True
        spec=importlib.util.spec_from_file_location('_teyvat_readonly_index',script)
        self.engine=importlib.util.module_from_spec(spec); spec.loader.exec_module(self.engine)
        self.engine.ROOT=self.root; self.engine.INDEX=self.dbpath.parent; self.engine.DB=self.dbpath
        self.engine.connect=self.connect
        self.verify_file(self.engine.INDEX/'实体别名.json'); self.verify_file(self.engine.INDEX/'已校订结论.json')
        from .retrieval_cache import prepare
        self.dbpath=prepare(self,lock)
        self.engine.DB=self.dbpath
    def connect(self,*args):
        db=sqlite3.connect(self.dbpath.as_uri()+'?mode=ro&immutable=1',uri=True); db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON'); return db
    def verify_file(self,path):
        path=Path(path).resolve()
        if not path.is_relative_to(self.root):raise ValueError('仅能读取锁定知识库内的资料。')
        rel=path.relative_to(self.root).as_posix(); record=self.files.get(rel)
        if not record or not path.is_file():raise ValueError('文件不属于锁定资料。')
        if path.stat().st_size!=record['bytes'] or fingerprint(path)!=record['sha256']:
            raise ValueError('资料在锁定后发生变化，请先核查，应用不会重写或自动更新。')
        return path
    def call(self,name,args):
        from argparse import Namespace
        with self.guard:
            result=[]; self.engine.emit=result.append
            try: getattr(self.engine,name)(Namespace(**args))
            except SystemExit as e:raise ValueError(str(e)) from e
            finally:self.engine.emit=None
            return result[0]
    def search(self,query,layer='summary',path=None,limit=6):
        if not isinstance(query,str) or not 1<=len(query)<=400:raise ValueError('检索词长度应为1—400字。')
        if layer not in ('summary','source','all'):raise ValueError('未知检索层。')
        result=self.call('search',dict(query=query,layer=layer,path=path,limit=min(max(int(limit),1),12),per_file=2,snippet=700))
        result['results']=[item for item in result['results'] if item['path'] in self.files]
        for item in result['results']:
            item.pop('absolute_path',None)
            item['citation']=self.citation(item['path'],item['lines'][0],item['title'])
        return result
    @staticmethod
    def citation(path,line,label='来源'):
        return f'[{label}](kb://s/{hashlib.sha256(path.encode()).hexdigest()[:20]}#L{line})'
    def read(self,path,start=1,count=80):
        path=getattr(self,'references',{}).get(path,path)
        p=self.verify_file(self.root/path)
        if p.suffix not in ('.md','.txt','.wiki'):raise ValueError('只能读取文字资料。')
        start=max(1,int(start));count=min(max(int(count),1),200)
        all_lines=p.read_text(encoding='utf-8-sig').splitlines(); end=min(len(all_lines),start+count-1)
        # A single wiki field may be very long. Return an explicit bound, never claim completeness.
        rows=[];size=0
        for n in range(start,end+1):
            text=all_lines[n-1]
            if size+len(text)>36000:
                rows.append({'line':n,'text':text[:max(0,36000-size)],'truncated':True});break
            rows.append({'line':n,'text':text});size+=len(text)
        rel=p.relative_to(self.root).as_posix()
        return {'path':rel,'total_lines':len(all_lines),'lines':rows,'citation':self.citation(rel,start,p.stem)}
    def find(self,query,limit=15):
        if not isinstance(query,str) or not 1<=len(query)<=100:raise ValueError('请输入简短标题关键词。')
        found=[p for p in self.files if query.casefold() in p.casefold() and p.endswith(('.md','.txt','.wiki'))]
        return {'paths':found[:min(int(limit),30)],'total':len(found)}
    def related(self,path):
        self.verify_file(self.root/path); return self.call('related',dict(path=path))
    def run(self,name,args):
        funcs={'search_lore':self.search,'read_source':self.read,'find_titles':self.find,'related_sources':self.related}
        if name not in funcs:raise ValueError('未知工具。')
        return funcs[name](**args)
