"""Validate the actual Windows WebView2 host, then close our test window/server."""
import json, socket, sys, threading, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn, webview
from twwiki.server import create_app
from twwiki.store import Store
from test_app import MemoryKeys, FakeProvider

out=Path(__file__).resolve().parents[1]/'test-results';out.mkdir(exist_ok=True)
keys=MemoryKeys();keys.value=''
app=create_app(store=Store(out/'desktop-state.sqlite3'),provider=FakeProvider([]),keys=keys)
sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
server=uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False))
thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
for _ in range(100):
    if server.started:break
    time.sleep(.05)
window=webview.create_window('原神百科 · 桌面验证',f'http://127.0.0.1:{port}/#token={app.state.token}',hidden=True,width=1100,height=800)
result={}
def check():
    try:
        window.events.loaded.wait(20)
        for _ in range(100):
            state=window.evaluate_js("({title:document.title, ready:document.querySelector('#locked-count')?.textContent, settings:document.querySelector('#settings')?.open})")
            if state and '47,202' in (state.get('ready') or '') and state.get('settings'):
                result.update(state);result['passed']=True;break
            time.sleep(.1)
        if not result:result.update({'passed':False,'reason':'Native page did not initialize.'})
    except Exception as e:result.update({'passed':False,'error':str(e)})
    finally:window.destroy()
try:webview.start(check,gui='edgechromium',private_mode=True)
finally:server.should_exit=True;thread.join(10);sock.close()
(out/'桌面窗口验收.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
if not result.get('passed'):raise SystemExit(1)
