import json
import os
import httpx

BASE='https://api.deepseek.com'

class ProviderError(Exception):
    pass

class Keys:
    """Read credentials into memory; never copy them into SQLite or config files."""
    service='TeyvatArchive'; account='deepseek-api-key'
    def __init__(self):
        self.value='';self.saved=False;self.source='none'
        try:
            import keyring
            self.value=keyring.get_password(self.service,self.account) or '';self.saved=bool(self.value)
        except Exception:pass
        if self.value:self.source='keyring'
        else:
            self.value=os.environ.get('DEEPSEEK_API_KEY','').strip()
            if self.value:self.source='environment'
    def set(self,value,persist=False):
        import keyring
        if persist:
            try:keyring.set_password(self.service,self.account,value)
            except Exception as e:raise ValueError('系统凭据存储不可用。取消“记住密钥”可仅在本次运行使用。') from e
        elif self.saved:
            try:keyring.delete_password(self.service,self.account)
            except Exception as e:raise ValueError('无法删除之前保存的密钥，请检查系统凭据权限。') from e
        self.value=value;self.saved=persist;self.source='keyring' if persist else 'session'
    def clear(self):
        if self.saved:
            import keyring
            try:keyring.delete_password(self.service,self.account)
            except Exception as e:raise ValueError('系统凭据删除失败，尚未清除保存的密钥。') from e
        self.value='';self.saved=False;self.source='none'

class DeepSeek:
    def __init__(self,client=None):
        self.client=client or httpx.AsyncClient(timeout=httpx.Timeout(connect=20,read=600,write=120,pool=20),follow_redirects=False)
    async def close(self):await self.client.aclose()
    @staticmethod
    def check(response):
        if response.status_code<400:return
        codes={400:'请求超出模型限制或参数不被接受。完整历史已保留；可查看上下文或新建对话。',
               401:'API Key 无效，请在设置中重新填写。',402:'DeepSeek 账户余额不足。',
               403:'账户无权使用该模型。',404:'模型或接口不可用，请核对模型设置。',
               429:'DeepSeek 请求繁忙或达到限额，请稍后重试。',500:'DeepSeek 服务暂时异常，请稍后重试。',503:'DeepSeek 服务繁忙，请稍后重试。'}
        raise ProviderError(codes.get(response.status_code,f'DeepSeek 返回错误 {response.status_code}。'))
    async def models(self,key):
        r=await self.client.get(BASE+'/models',headers={'Authorization':'Bearer '+key});self.check(r)
        return [x['id'] for x in r.json().get('data',[]) if isinstance(x.get('id'),str)]
    async def stream(self,key,payload):
        async with self.client.stream('POST',BASE+'/chat/completions',headers={'Authorization':'Bearer '+key},json=payload) as response:
            if response.status_code>=400:
                await response.aread();self.check(response)
            done=False
            async for line in response.aiter_lines():
                if not line.startswith('data:'):continue
                raw=line[5:].strip()
                if raw=='[DONE]':done=True;break
                if not raw:continue
                try:chunk=json.loads(raw)
                except json.JSONDecodeError as e:raise ProviderError('响应格式不完整，已保存收到的内容。') from e
                if 'error' in chunk:raise ProviderError('模型流式响应报错，已保存收到的内容。')
                yield chunk
            if not done:raise ProviderError('连接在回答完成前中断，已保存收到的内容。')
