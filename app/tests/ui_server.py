"""Only for browser tests: deterministic provider, isolated disposable user data."""
import asyncio, json, os, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from twwiki.server import create_app
from twwiki.store import Store
from twwiki.knowledge import Knowledge
from test_app import MemoryKeys, chunk
import uvicorn

class TestProvider:
    async def models(self,key):return ['deepseek-flash','deepseek-v4-pro']
    async def close(self):pass
    async def stream(self,key,payload):
        messages=payload['messages'];last=messages[-1]
        if messages[0].get('role')=='system' and '独立的原神剧情证据审阅者' in messages[0].get('content',''):
            yield chunk('模拟审阅：身份对应已与原文核对。',reasoning='这是模拟的独立证据审阅过程。',finish='stop');return
        if last['role']=='user' and '停止测试' in last['content']:
            yield chunk('这是一段用于停止操作的测试内容。',reasoning='界面自动化测试。')
            await asyncio.sleep(600);return
        if 'tools' not in payload:
            yield chunk('这是原生模式的模拟响应，没有知识库与记忆注入。',finish='stop');return
        if last['role']=='system':
            data=json.loads(next(m['content'] for m in reversed(messages) if m['role']=='tool'))
            yield chunk('**界面模拟回答**：皮耶罗曾任深秘院副院长，海洛塔帝任院长。\n\n'+data.get('citation',''),reasoning='这是界面模拟接口的复核思考。',finish='stop',usage={'prompt_tokens':920,'completion_tokens':45,'total_tokens':965})
            return
        if last['role']=='user':
            yield chunk(reasoning='这是自动化界面测试，先读取指定来源。')
            await asyncio.sleep(1)
            yield chunk(reasoning='继续核对上下文，显示的是接口实际返回的思考字段。')
            await asyncio.sleep(1)
            yield chunk(tools=[{'index':0,'id':'ui-call-'+str(len(messages)), 'function':{'name':'read_source','arguments':json.dumps({'path':'10-剧情/07-空月之歌/05 空月之歌 第四幕 回望湮灭的月光.md','start':740,'count':5},ensure_ascii=False)}}],finish='tool_calls');return
        data=json.loads(last['content']);citation=data.get('citation','')
        for text in ['**界面模拟回答**：','皮耶罗曾任深秘院副院长，','海洛塔帝任院长。\n\n',citation]:
            yield chunk(text);await asyncio.sleep(.08)
        yield chunk(finish='stop',usage={'prompt_tokens':920,'completion_tokens':45,'total_tokens':965})

if __name__=='__main__':
    keys=MemoryKeys();keys.value=''
    app=create_app(store=Store(Path(os.environ['TEYVAT_UI_STATE'])),provider=TestProvider(),keys=keys,token='ui-test-session')
    uvicorn.run(app,host='127.0.0.1',port=18766,log_level='warning',access_log=False)
