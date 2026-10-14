import json, sqlite3, time, uuid
from pathlib import Path

DEFAULTS = {'model':'deepseek-flash', 'effort':'default', 'max_tokens':None, 'tool_rounds':32, 'review':False}

class Store:
    def __init__(self, path:Path):
        path.parent.mkdir(parents=True, exist_ok=True); self.path=path
        with self.connect() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS chats(id TEXT PRIMARY KEY,title TEXT,created REAL,updated REAL,mode TEXT NOT NULL DEFAULT 'lore');
            CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,chat_id TEXT,seq INTEGER,wire TEXT,meta TEXT,created REAL);
            CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id,seq);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY,text TEXT,created REAL);
            ''')
    def connect(self):
        db=sqlite3.connect(self.path, timeout=15); db.row_factory=sqlite3.Row; return db
    def chats(self):
        with self.connect() as db: return [dict(r) for r in db.execute('SELECT * FROM chats ORDER BY updated DESC')]
    def chat(self, cid):
        with self.connect() as db:
            row=db.execute('SELECT * FROM chats WHERE id=?',(cid,)).fetchone()
            if not row: raise KeyError(cid)
            return dict(row)
    def create(self, title='新的对话',mode='lore'):
        cid=uuid.uuid4().hex; now=time.time()
        with self.connect() as db: db.execute('INSERT INTO chats VALUES(?,?,?,?,?)',(cid,title,now,now,mode))
        return self.chat(cid)
    def rename(self,cid,title):
        self.chat(cid)
        with self.connect() as db: db.execute('UPDATE chats SET title=?,updated=? WHERE id=?',(title[:100],time.time(),cid))
    def delete(self,cid):
        with self.connect() as db:
            db.execute('DELETE FROM messages WHERE chat_id=?',(cid,)); db.execute('DELETE FROM chats WHERE id=?',(cid,))
    def messages(self,cid):
        self.chat(cid)
        with self.connect() as db:
            return [{'id':r['id'],'wire':json.loads(r['wire']),'meta':json.loads(r['meta']),'created':r['created']} for r in db.execute('SELECT * FROM messages WHERE chat_id=? ORDER BY seq',(cid,))]
    def append(self,cid,wire,meta=None):
        self.chat(cid); mid=uuid.uuid4().hex; now=time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            seq=db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM messages WHERE chat_id=?',(cid,)).fetchone()[0]
            db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',(mid,cid,seq,json.dumps(wire,ensure_ascii=False),json.dumps(meta or {},ensure_ascii=False),now))
            db.execute('UPDATE chats SET updated=? WHERE id=?',(now,cid))
        return mid
    def settings(self):
        with self.connect() as db:
            row=db.execute("SELECT value FROM settings WHERE key='model'").fetchone()
        return DEFAULTS | (json.loads(row[0]) if row else {})
    def set_settings(self,value):
        with self.connect() as db: db.execute("INSERT OR REPLACE INTO settings VALUES('model',?)",(json.dumps(value),))
    def memories(self):
        with self.connect() as db:return [dict(r) for r in db.execute('SELECT * FROM memories ORDER BY created')]
    def remember(self,text):
        mid=uuid.uuid4().hex
        with self.connect() as db:db.execute('INSERT INTO memories VALUES(?,?,?)',(mid,text,time.time()))
        return mid
    def forget(self,mid):
        with self.connect() as db:db.execute('DELETE FROM memories WHERE id=?',(mid,))
