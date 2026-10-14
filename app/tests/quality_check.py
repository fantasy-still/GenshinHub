"""Opt-in, paid, independent questions. Reference answers are never sent to the model."""
import argparse,asyncio,json,sys,time,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from twwiki.agent import Agent
from twwiki.knowledge import Knowledge
from twwiki.provider import DeepSeek,Keys,ProviderError
from twwiki.store import Store

QUESTIONS=[
    '丑角在坎瑞亚深秘院担任什么职务？当时院长是谁？请回查剧情原文，简要回答并给出出处。',
    '以《罪人舞步旋》为范围，芙卡洛斯的计划怎样拯救枫丹？芙宁娜和那维莱特分别承担什么作用，为什么摧毁神座是关键？请把因果讲清楚。',
    '散兵从世界树中删除自己后，丹羽等人复活了吗？结合《倾落伽蓝》，解释历史、记忆和散兵责任的区别。',
]

async def main(only_case=None,review=False):
    out=Path(__file__).resolve().parents[1]/'test-results';out.mkdir(exist_ok=True)
    keys=Keys()
    if not keys.value:raise SystemExit('Missing credential; no request sent.')
    provider=DeepSeek();store=Store(out/('quality-'+uuid.uuid4().hex+'.sqlite3'))
    store.set_settings(store.settings()|{'review':review})
    knowledge=Knowledge();agent=Agent(store,knowledge,provider,keys)
    report={'live':True,'reference_answers_sent':False,'settings':store.settings(),'cases':[]}
    try:
        for index,question in enumerate(QUESTIONS,1):
            if only_case is not None and index!=only_case:continue
            cid=store.create()['id'];start=time.monotonic();agent.reserve(cid);tools=[];last='';error=None
            try:
                async with asyncio.timeout(360):
                    async for event in agent.run(cid,question):
                        last=event['type']
                        if last=='tool':
                            tools.append({'name':event['name'],'args':event['args']})
                            print(json.dumps({'case':index,'tool':event['name']},ensure_ascii=False),flush=True)
                        if last in ('error','notice'):error=event['text']
            except Exception as e:error=type(e).__name__
            assistants=[r for r in store.messages(cid) if r['wire']['role']=='assistant']
            final=assistants[-1] if assistants else {'wire':{},'meta':{}}
            case={'case_id':index,'question':question,'seconds':round(time.monotonic()-start,2),'tools':tools,
                  'answer':final['wire'].get('content',''),'meta':final['meta'],
                  'reasoning_characters':sum(len(m['wire'].get('reasoning_content','')) for m in assistants),
                  'usage_per_request':[m['meta'].get('usage') for m in assistants],
                  'transport_completed':last=='done' and not error,'error':error}
            report['cases'].append(case)
            mode='独立复核' if review else '标准研究'
            filename=f'同题真实回答-{mode}-重试{only_case}.json' if only_case else f'同题真实回答-{mode}.json'
            (out/filename).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'case':index,'completed':case['transport_completed'],'seconds':case['seconds'],'citations_valid':all(c['valid'] for c in final['meta'].get('citations',[]))},ensure_ascii=False),flush=True)
    finally:await provider.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--case',type=int,choices=(1,2,3));parser.add_argument('--review',action='store_true');args=parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8');asyncio.run(main(args.case,args.review))
