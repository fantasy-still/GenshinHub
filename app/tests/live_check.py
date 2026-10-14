"""Opt-in paid integration check; never part of pytest or UI tests.

Reads DEEPSEEK_API_KEY through Keys; writes only isolated chat/results, no secrets.
"""
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from twwiki.agent import Agent
from twwiki.knowledge import Knowledge
from twwiki.provider import DeepSeek, Keys, ProviderError
from twwiki.store import Store

async def main():
    out=Path(__file__).resolve().parents[1]/'test-results'
    out.mkdir(exist_ok=True)
    keys=Keys()
    if not keys.value:raise SystemExit('Missing DeepSeek credential; no request sent.')
    provider=DeepSeek()
    report={'live':True,'key_source':keys.source,'requested_model':'deepseek-flash','turns':[]}
    try:
        report['models']=await provider.models(keys.value)
        print(json.dumps({'models':report['models']},ensure_ascii=False),flush=True)
        if 'deepseek-flash' not in report['models']:raise ProviderError('默认 Flash 模型不可用。')
        store=Store(out/('live-'+uuid.uuid4().hex+'.sqlite3'))
        agent=Agent(store,Knowledge(),provider,keys)
        cid=store.create()['id']
        for prompt in ['丑角在坎瑞亚深秘院担任什么职务？当时院长是谁？请回查剧情原文，简要回答并给出出处。','你刚才提到的两个人，分别对应哪个职务？请沿用上轮已经核实的依据，简短回答。']:
            start=time.monotonic();before=len(store.messages(cid));agent.reserve(cid)
            events=[];calls=[]
            async with asyncio.timeout(360):
                async for e in agent.run(cid,prompt):
                    events.append(e['type'])
                    if e['type']=='tool':
                        calls.append(e['name']);print(json.dumps({'tool':e['name']},ensure_ascii=False),flush=True)
                    if e['type']=='error':raise ProviderError(e['text'])
            records=store.messages(cid)[before:]
            answers=[m for m in records if m['wire']['role']=='assistant']
            final=answers[-1]
            turn={'question':prompt,'seconds':round(time.monotonic()-start,2),'tools':calls,
                  'answer':final['wire']['content'],'actual_model':final['meta'].get('actual_model'),
                  'finish_reason':final['meta'].get('finish_reason'),
                  'usage_per_request':[m['meta'].get('usage') for m in answers],
                  'reasoning_preserved':all('reasoning_content' in m['wire'] for m in answers),
                  'native_parameters_omitted':all(not ({'reasoning_effort','max_tokens','temperature','top_p'} & m['meta']['request_settings'].keys()) for m in answers)}
            report['turns'].append(turn)
            if events[-1]!='done':raise ProviderError('模型未正常完成回答。')
            print(json.dumps({'turn':len(report['turns']),'seconds':turn['seconds'],'completed':True},ensure_ascii=False),flush=True)
        report['passed']=all(t['finish_reason']=='stop' and 'kb://' in t['answer'] and '副院长' in t['answer'] for t in report['turns']) and 'read_source' in report['turns'][0]['tools']
    except Exception as e:
        report['passed']=False
        report['error']=str(e) if isinstance(e,ProviderError) else type(e).__name__
    finally:
        await provider.close()
        (out/'真实接口验收.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'error':report.get('error')},ensure_ascii=False),flush=True)
    return 0 if report['passed'] else 1

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(asyncio.run(main()))
