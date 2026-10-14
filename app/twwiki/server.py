import asyncio, hmac, json, secrets
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from markdown_it import MarkdownIt
from pydantic import BaseModel, Field
from .paths import APP, ROOT, STATE
from .store import Store
from .knowledge import Knowledge
from .provider import DeepSeek, Keys, ProviderError
from .agent import Agent

class KeyInput(BaseModel):
    key:str=Field(min_length=8,max_length=512)
    persist:bool=False
class ChatInput(BaseModel):
    mode:Literal['lore','direct']='lore'
class RenameInput(BaseModel):
    title:str=Field(min_length=1,max_length=100)
class MessageInput(BaseModel):
    text:str=Field(min_length=1,max_length=2000000)
class MemoryInput(BaseModel):
    text:str=Field(min_length=1,max_length=3000)
class SettingsInput(BaseModel):
    model:str=Field(default='deepseek-flash',pattern=r'^deepseek-[a-z0-9.-]{1,60}$')
    effort:Literal['default','none','low','high','max']='default'
    max_tokens:int|None=Field(default=None,ge=1,le=393216)
    tool_rounds:int=Field(default=32,ge=1,le=128)
    review:bool=False

def create_app(store=None,knowledge=None,provider=None,keys=None,token=None):
    store=store or Store(STATE/'conversations.sqlite3'); knowledge=knowledge or Knowledge()
    provider=provider or DeepSeek();keys=keys or Keys();token=token or secrets.token_urlsafe(32)
    agent=Agent(store,knowledge,provider,keys); tasks=set()
    renderer=MarkdownIt('commonmark',{'html':False}).enable('table').disable('image')
    @asynccontextmanager
    async def lifespan(app):
        yield
        for cid in list(agent.busy):agent.cancel(cid)
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        await provider.close()
    app=FastAPI(title='原神百科',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.token=token;app.state.agent=agent;app.state.store=store;app.state.keys=keys
    @app.middleware('http')
    async def local_only(request,call_next):
        host=request.url.hostname
        if host not in ('127.0.0.1','localhost','testserver'):return JSONResponse({'detail':'仅允许本机访问。'},403)
        origin=request.headers.get('origin')
        if origin and origin!=str(request.base_url).rstrip('/'):
            return JSONResponse({'detail':'来源不被允许。'},403)
        if request.url.path.startswith('/api/') and not hmac.compare_digest(request.headers.get('x-app-token',''),token):
            return JSONResponse({'detail':'会话验证失败，请从启动入口打开。'},401)
        response=await call_next(request)
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Cache-Control']='no-store'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        return response
    @app.exception_handler(KeyError)
    async def not_found(request,exc):return JSONResponse({'detail':'对话不存在。'},404)
    @app.exception_handler(ValueError)
    async def bad_input(request,exc):return JSONResponse({'detail':str(exc)},400)
    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        # Do not echo submitted secrets in validation errors.
        return JSONResponse({'detail':'输入格式或参数范围不正确，请检查后重试。'},422)
    @app.get('/')
    async def home():return FileResponse(APP/'static/index.html')
    app.mount('/static',StaticFiles(directory=APP/'static'),name='static')
    @app.get('/api/status')
    async def status():
        return {'has_key':bool(keys.value),'key_saved':keys.saved,'key_source':getattr(keys,'source','session'),'settings':store.settings(),'busy':list(agent.busy),
                'knowledge':{'locked':True,'files':len(knowledge.files),'snapshot':'2026-10-13'},
                'model_info':{'alias':'deepseek-flash','version':'DeepSeek-V4.1-Flash','context':1000000,'max_output':393216,'checked':'2026-10-13'}}
    @app.post('/api/key')
    async def set_key(body:KeyInput):
        key=body.key.strip()
        if any(c.isspace() for c in key):raise ValueError('API Key 中不应包含空白。')
        try:models=await provider.models(key)
        except ProviderError as e:raise HTTPException(400,str(e))
        except Exception:raise HTTPException(502,'无法连接 DeepSeek，请检查网络。密钥尚未保存。')
        await asyncio.to_thread(keys.set,key,body.persist)
        return {'has_key':True,'key_saved':keys.saved,'models':models,'default_available':'deepseek-flash' in models}
    @app.delete('/api/key')
    async def clear_key():
        if agent.busy:raise HTTPException(409,'请先停止正在进行的回答。')
        await asyncio.to_thread(keys.clear);return {'ok':True}
    @app.get('/api/models')
    async def models():
        if not keys.value:raise HTTPException(400,'请先输入 API Key。')
        try:return {'models':await provider.models(keys.value)}
        except Exception:raise HTTPException(502,'无法获取可用模型，请检查密钥或网络。')
    @app.put('/api/settings')
    async def settings(body:SettingsInput):store.set_settings(body.model_dump());return store.settings()
    @app.get('/api/chats')
    async def chats():return store.chats()
    @app.post('/api/chats')
    async def new_chat(body:ChatInput):return store.create(mode=body.mode)
    @app.patch('/api/chats/{cid}')
    async def rename(cid:str,body:RenameInput):store.rename(cid,body.title.strip() or '新的对话');return {'ok':True}
    @app.delete('/api/chats/{cid}')
    async def delete(cid:str):
        if cid in agent.busy:raise HTTPException(409,'请先停止这段对话的回答。')
        store.delete(cid);return {'ok':True}
    @app.get('/api/chats/{cid}')
    async def chat(cid:str):
        records=store.messages(cid)
        for record in records:
            wire=record['wire'];record['html']=renderer.render(wire.get('content') or '') if wire['role']=='assistant' else ''
        return {'chat':store.chat(cid),'messages':records,'busy':cid in agent.busy}
    @app.get('/api/chats/{cid}/export')
    async def export(cid:str):
        return {'chat':store.chat(cid),'messages':store.messages(cid),'note':'包含完整模型响应字段和工具结果；不包含API Key。'}
    @app.get('/api/chats/{cid}/context')
    async def context(cid:str):
        messages=agent.context(cid)
        records=store.messages(cid);usage=next((m['meta'].get('usage') for m in reversed(records) if m['meta'].get('usage')),None)
        return {'messages':len(messages),'characters':sum(len(json.dumps(m,ensure_ascii=False)) for m in messages),'last_usage':usage,
                'compression':'无自动压缩或截断','memory_enabled':store.chat(cid)['mode']=='lore',
                'note':'用量是最近一次 API 请求返回的实际值，不是下一次请求的精确计数。达到上限时保留历史并报告错误。'}
    @app.post('/api/chats/{cid}/stop')
    async def stop(cid:str):agent.cancel(cid);return {'ok':True}
    @app.post('/api/chats/{cid}/send')
    async def send(cid:str,body:MessageInput,request:Request):
        store.chat(cid)
        if not keys.value:raise HTTPException(400,'请先在设置中输入 DeepSeek API Key。')
        if not body.text.strip():raise ValueError('消息不能为空。')
        try:agent.reserve(cid)
        except ValueError as e:raise HTTPException(409,str(e))
        async def events():
            try:
                async for event in agent.run(cid,body.text):
                    if await request.is_disconnected():agent.cancel(cid)
                    yield 'data: '+json.dumps(event,ensure_ascii=False)+'\n\n'
            finally:agent.cancel(cid);agent.release(cid)
        return StreamingResponse(events(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})
    @app.get('/api/memories')
    async def memories():return store.memories()
    @app.post('/api/memories')
    async def remember(body:MemoryInput):
        if len(store.memories())>=50:raise ValueError('已保存50条记忆，请先整理或删除旧记忆。')
        if not body.text.strip():raise ValueError('记忆不能为空。')
        return {'id':store.remember(body.text.strip())}
    @app.delete('/api/memories/{mid}')
    async def forget(mid:str):store.forget(mid);return {'ok':True}
    @app.get('/api/source')
    async def source(path:str,start:int=1,count:int=100):return await asyncio.to_thread(knowledge.read,path,start,count)
    @app.get('/api/search')
    async def search(q:str,layer:Literal['all','summary','source']='summary'):
        return await asyncio.to_thread(knowledge.search,q,layer)
    return app
