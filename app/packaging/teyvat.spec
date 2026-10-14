# Run from app/: python -m PyInstaller packaging/teyvat.spec --noconfirm
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

app = Path(SPECPATH).parent
datas = [(str(app / 'static'), 'static'),
         (str(app / 'knowledge.lock.json'), '.')]
datas += copy_metadata('keyring', recursive=True)
a = Analysis([str(app / 'packaging/entry.py')], pathex=[str(app)],
             datas=datas, binaries=[],
             hiddenimports=collect_submodules('keyring.backends') +
                           ['concurrent.futures', 'sqlite3', 'uvicorn.logging',
                            'uvicorn.loops.auto', 'uvicorn.protocols.http.h11_impl',
                            'uvicorn.lifespan.on'],
             excludes=['pytest', 'playwright', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
                       'tkinter', 'IPython', 'matplotlib', 'numpy'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='GenshinHub',
          debug=False, strip=False, upx=False, console=True,
          icon=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
               name='GenshinHub')
