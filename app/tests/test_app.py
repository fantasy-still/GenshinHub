import asyncio, json, sqlite3
from types import SimpleNamespace
import httpx, pytest
import keyring
from fastapi.testclient import TestClient
from twwiki.store import Store
from twwiki.agent import Agent
from twwiki.provider import DeepSeek, ProviderError
from twwiki.server import create_app
from twwiki.knowledge import Knowledge

def test_environment_key_is_memory_only_and_clear_is_session_scoped(monkeypatch,store):
    from twwiki.provider import Keys
    monkeypatch.setenv('DEEPSEEK_API_KEY','  environment-test-credential  ')
    monkeypatch.setattr(keyring,'get_password',lambda *a:None)
    def forbidden(*a):raise AssertionError('Environment credentials must not be persisted')
    monkeypatch.setattr(keyring,'set_password',forbidden)
    monkeypatch.setattr(keyring,'delete_password',forbidden)
    keys=Keys()
    assert keys.value=='environment-test-credential' and keys.source=='environment' and not keys.saved
    with TestClient(create_app(store,FakeKnowledge(),FakeProvider([]),keys,token='test-session')) as c:
        response=c.get('/api/status',headers={'x-app-token':'test-session'})
        assert response.json()['key_source']=='environment'
        assert keys.value not in response.text
        assert c.delete('/api/key',headers={'x-app-token':'test-session'}).status_code==200
    assert keys.value=='' and keys.source=='none'
    assert Keys().value=='environment-test-credential'
    monkeypatch.setattr(keyring,'get_password',lambda *a:'saved-test-credential')
    assert Keys().source=='keyring' and Keys().saved

class MemoryKeys:
    value='test-only-key';saved=False
    def set(self,value,persist=False):self.value=value;self.saved=persist
    def clear(self):self.value='';self.saved=False

class FakeKnowledge:
    files={'example.md':{}}
    def run(self,name,args):return {'path':'example.md','lines':[{'line':1,'text':'一段来源'}]}
    def read(self,*a):return {'path':'example.md','lines':[{'line':1,'text':'一段来源'}]}
    def search(self,*a):return {'results':[]}

def chunk(content='',reasoning='',tools=None,finish=None,usage=None):
    d={'content':content,'reasoning_content':reasoning}
    if tools:d['tool_calls']=tools
    return {'model':'deepseek-flash','choices':[{'delta':d,'finish_reason':finish}],'usage':usage}

class FakeProvider:
    def __init__(self,rounds):self.rounds=iter(rounds);self.requests=[]
    async def stream(self,key,payload):
        self.requests.append(json.loads(json.dumps(payload)))
        for c in next(self.rounds):yield c
    async def models(self,key):return ['deepseek-flash','deepseek-v4-pro']
    async def close(self):pass

@pytest.fixture
def store(tmp_path):
    s=Store(tmp_path/'state.sqlite3');s.set_settings(s.settings()|{'review':False});return s

async def test_review_preserves_draft_and_native_wire_history(store):
    store.set_settings(store.settings()|{'review':True})
    p=FakeProvider([[chunk('初稿',reasoning='初次推理',finish='stop')],[chunk('一处因果需核对',reasoning='独立审阅推理',finish='stop')],[chunk('核查后的最终回答',reasoning='复核推理',finish='stop')]])
    a=Agent(store,FakeKnowledge(),p,MemoryKeys());cid=store.create()['id']
    events=await run(a,cid,'请解释事件原因')
    records=store.messages(cid)
    assert records[1]['meta']['draft'] and records[2]['meta']['internal_review']
    assert records[3]['meta']['review_notes']
    assert records[5]['meta']['reviewed'] and records[5]['wire']['content']=='核查后的最终回答'
    assert len(p.requests[1]['messages'])==2 and 'tools' not in p.requests[1]
    assert json.loads(p.requests[1]['messages'][1]['content'])['draft']=='初稿'
    assert p.requests[2]['messages'][2]['reasoning_content']=='初次推理'
    assert p.requests[2]['messages'][4]['reasoning_content']=='独立审阅推理'
    assert any(e['type']=='review' for e in events) and events[-1]['type']=='done'
    assert 'reasoning_effort' not in p.requests[1] and 'max_tokens' not in p.requests[1]

async def run(agent,cid,text):
    agent.reserve(cid)
    return [e async for e in agent.run(cid,text)]

async def test_complete_wire_history_reasoning_and_tool_arguments(store):
    provider=FakeProvider([
        [chunk(reasoning='第一段推理',tools=[{'index':0,'id':'call_1','function':{'name':'read_source','arguments':'{"path":"example.md",'}}]),
         chunk(tools=[{'index':0,'function':{'arguments':'"start":1}'}}],finish='tool_calls')],
        [chunk('回答一','补充推理',finish='stop',usage={'prompt_tokens':100,'completion_tokens':20,'total_tokens':120})],
        [chunk('回答二',finish='stop')]])
    a=Agent(store,FakeKnowledge(),provider,MemoryKeys());cid=store.create()['id']
    await run(a,cid,'第一个问题');await run(a,cid,'第二个问题')
    sent=provider.requests[-1]['messages']
    assert sent[2]['reasoning_content']=='第一段推理'
    assert sent[2]['tool_calls'][0]['function']['arguments']=='{"path":"example.md","start":1}'
    assert sent[3]['tool_call_id']=='call_1' and sent[3]['role']=='tool'
    assert sent[4]['reasoning_content']=='补充推理'
    assert sent[-1]['content']=='第二个问题'
    assert 'max_tokens' not in provider.requests[0] and 'reasoning_effort' not in provider.requests[0]
    assert store.messages(cid)[3]['meta']['usage']['total_tokens']==120
    assert cid not in a.busy

async def test_native_mode_no_prompt_tools_or_memory(store):
    store.remember('只用简短回答');p=FakeProvider([[chunk('好',finish='stop')]])
    a=Agent(store,FakeKnowledge(),p,MemoryKeys());cid=store.create(mode='direct')['id'];await run(a,cid,'你好')
    assert p.requests[0]['messages']==[{'role':'user','content':'你好'}]
    assert 'tools' not in p.requests[0]

async def test_cancel_preserves_partial_and_releases(store):
    class Slow(FakeProvider):
        async def stream(self,key,payload):
            yield chunk('已收到的内容',reasoning='已收到的推理')
            await asyncio.sleep(600)
    a=Agent(store,FakeKnowledge(),Slow([]),MemoryKeys());cid=store.create()['id']
    a.reserve(cid);events=[]
    async for e in a.run(cid,'开始'):
        events.append(e)
        if e['type']=='token':a.cancel(cid)
    assert events[-1]['type']=='cancelled'
    assert store.messages(cid)[-1]['wire']['content']=='已收到的内容'
    assert store.messages(cid)[-1]['meta']['interrupted']
    assert cid not in a.busy

async def test_crash_repairs_unanswered_tool_ids(store):
    cid=store.create()['id'];store.append(cid,{'role':'assistant','content':'','reasoning_content':'原始推理','tool_calls':[{'id':'pending','type':'function','function':{'name':'read_source','arguments':'{}'}}]})
    p=FakeProvider([[chunk('恢复完成',finish='stop')]]);a=Agent(store,FakeKnowledge(),p,MemoryKeys());await run(a,cid,'继续')
    messages=p.requests[0]['messages'];assert messages[2]['tool_call_id']=='pending';assert messages[3]['role']=='user'

async def test_limit_does_not_drop_pending_calls(store):
    store.set_settings(store.settings()|{'tool_rounds':1})
    call=lambda n:chunk(tools=[{'index':0,'id':str(n),'function':{'name':'read_source','arguments':'{"path":"example.md","start":1}'}}],finish='tool_calls')
    p=FakeProvider([[call(1)],[call(2)]]);a=Agent(store,FakeKnowledge(),p,MemoryKeys());cid=store.create()['id'];events=await run(a,cid,'问题')
    assert events[-1]['type']=='notice';assert store.messages(cid)[-1]['wire']['tool_call_id']=='2'

def test_sqlite_persistence_memories_and_delete(store):
    cid=store.create()['id'];store.append(cid,{'role':'user','content':'第一次'});mid=store.remember('关注坎瑞亚')
    restored=Store(store.path);assert restored.messages(cid)[0]['wire']['content']=='第一次';assert restored.memories()[0]['text']=='关注坎瑞亚'
    restored.forget(mid);restored.delete(cid);assert restored.chats()==[] and restored.memories()==[]

def test_auth_origin_key_not_exported_and_markdown_safe(store):
    keys=MemoryKeys();p=FakeProvider([[chunk('<script>alert(1)</script> [坏链接](javascript:alert(1))',finish='stop')]])
    with TestClient(create_app(store,FakeKnowledge(),p,keys,token='test-session')) as c:
        assert c.get('/api/status').status_code==401
        c.headers['x-app-token']='test-session'
        assert c.post('/api/chats',json={},headers={'Origin':'https://evil.example'}).status_code==403
        assert c.get('/api/status',headers={'Host':'evil.example'}).status_code==403
        cid=c.post('/api/chats',json={}).json()['id']
        response=c.post('/api/chats/'+cid+'/send',json={'text':'问题'});assert '"type": "done"' in response.text
        record=c.get('/api/chats/'+cid).json()['messages'][-1]
        assert '<script>' not in record['html'];assert 'href="javascript:' not in record['html']
        export=c.get('/api/chats/'+cid+'/export').text;assert keys.value not in export
        assert c.put('/api/settings',json={'max_tokens':393217}).status_code==422

async def test_provider_sse_usage_and_api_error():
    def transport(request):
        assert request.url.host=='api.deepseek.com'
        if request.url.path.endswith('models'):return httpx.Response(401,json={'error':'secret server error should not be echoed'})
        text='\n'.join([': keepalive','data: '+json.dumps(chunk('内容',finish='stop',usage={'total_tokens':8})), '', 'data: [DONE]',''])
        return httpx.Response(200,text=text,headers={'content-type':'text/event-stream'})
    p=DeepSeek(httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    result=[c async for c in p.stream('secret',{'messages':[]})];assert result[-1]['usage']['total_tokens']==8
    with pytest.raises(ProviderError,match='API Key'):await p.models('secret')
    await p.close()

@pytest.fixture(scope='module')
def real_knowledge():return Knowledge()

def test_real_corpus_readonly_retrieval_and_path_boundary(real_knowledge):
    k=real_knowledge;r=k.search('皮耶罗 副院长','summary')
    assert any('人物与势力/04-' in x['path'] for x in r['results'])
    assert all(x['path'] in k.files for x in r['results'])
    with k.connect() as db:
        documents=list(db.execute('SELECT path,sha256 FROM documents'))
        assert all(p in k.files and sha==k.files[p]['sha256'] for p,sha in documents)
        assert any(p=='70-索引与检索/人物志/人物档案/09-愚人众与执行官/皮耶罗.md' for p,sha in documents)
    source=k.read('10-剧情/07-空月之歌/05 空月之歌 第四幕 回望湮灭的月光.md',740,5)
    assert any('副院长' in x['text'] for x in source['lines'])
    assert source['citation'].startswith('[') and 'kb://' in source['citation']
    with pytest.raises(ValueError):k.read('../Windows/win.ini')
    with pytest.raises(ValueError):k.read('app/.local/conversations.sqlite3')
    with k.connect() as db:
        with pytest.raises(sqlite3.OperationalError):db.execute('CREATE TABLE malicious(id)')

def test_citation_checks_actual_files_and_lines(real_knowledge):
    from twwiki.citations import audit
    valid=real_knowledge.citation('10-剧情/07-空月之歌/05 空月之歌 第四幕 回望湮灭的月光.md',740)
    missing=real_knowledge.citation('99-脚本与日志/不存在.md',1)
    out_of_bounds=real_knowledge.citation('10-剧情/07-空月之歌/05 空月之歌 第四幕 回望湮灭的月光.md',999999)
    assert [x['valid'] for x in audit(valid+' '+missing+' '+out_of_bounds,real_knowledge)]==[True,False,False]

def test_changed_corpus_is_rejected(tmp_path):
    from twwiki.freeze import fingerprint
    k=object.__new__(Knowledge);k.root=tmp_path
    p=tmp_path/'source.md';p.write_text('原文',encoding='utf-8');k.files={'source.md':{'sha256':fingerprint(p),'bytes':p.stat().st_size}}
    p.write_text('改文',encoding='utf-8')
    with pytest.raises(ValueError,match='变化'):k.read('source.md')

def test_single_writer_released_after_exit(tmp_path):
    from twwiki.instance import single_instance,AlreadyRunning
    with single_instance(tmp_path):
        with pytest.raises(AlreadyRunning):
            with single_instance(tmp_path):pass
    with single_instance(tmp_path):pass

async def test_length_finish_does_not_execute_incomplete_tool(store):
    p=FakeProvider([[chunk('部分内容',tools=[{'index':0,'id':'cut','function':{'name':'read_source','arguments':'{"path":'}}],finish='length')]])
    a=Agent(store,FakeKnowledge(),p,MemoryKeys());cid=store.create()['id'];events=await run(a,cid,'问题')
    assert events[-1]['finish_reason']=='length'
    assert not store.messages(cid)[-1]['wire'].get('tool_calls')
