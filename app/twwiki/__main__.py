import argparse, socket, threading, time, webbrowser, sys
import uvicorn
from .server import create_app
from .paths import STATE
from .instance import single_instance, AlreadyRunning

def launch():
    parser=argparse.ArgumentParser(description='原神百科');parser.add_argument('--browser',action='store_true');parser.add_argument('--serve',action='store_true');parser.add_argument('--port',type=int,default=0)
    args=parser.parse_args();app=create_app()
    sock=socket.socket();sock.bind(('127.0.0.1',args.port));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='warning',access_log=False))
    url=f'http://127.0.0.1:{port}/#token={app.state.token}'
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    for _ in range(150):
        if server.started:break
        if not thread.is_alive():raise RuntimeError('本地服务启动失败。')
        time.sleep(.1)
    else:raise RuntimeError('本地服务启动超时。')
    try:
        if args.serve:
            print(url,flush=True)
            while thread.is_alive():thread.join(1)
        elif args.browser:
            webbrowser.open(url)
            while thread.is_alive():thread.join(1)
        else:
            try:
                import webview
                webview.create_window('原神百科',url,width=1360,height=900,min_size=(800,600),background_color='#fcfcfa')
                webview.start(gui='edgechromium' if sys.platform=='win32' else None,private_mode=True)
            except Exception:
                webbrowser.open(url)
                print('桌面组件不可用，已在浏览器打开。按Ctrl+C停止服务。')
                while thread.is_alive():thread.join(1)
    except KeyboardInterrupt:pass
    finally:
        for cid in list(app.state.agent.busy):app.state.agent.cancel(cid)
        server.should_exit=True;thread.join(10);sock.close()

def main():
    # pythonw.exe has no standard streams; Uvicorn's logging formatter needs them.
    if sys.stdout is None or sys.stderr is None:
        STATE.mkdir(parents=True,exist_ok=True)
        log=(STATE/'startup.log').open('a',encoding='utf-8')
        sys.stdout=sys.stdout or log;sys.stderr=sys.stderr or log
    try:
        with single_instance(STATE):launch()
    except AlreadyRunning:
        text='原神百科已经在运行，请切换到现有窗口。'
        if sys.platform=='win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,text,'原神百科',0)
        else:print(text)

if __name__=='__main__':main()
