from pathlib import Path
import os
import sys

APP = Path(__file__).resolve().parents[1]
FROZEN = getattr(sys, 'frozen', False)
DEFAULT_ROOT = Path(sys.executable).resolve().parent / 'data' if FROZEN else APP.parent
DEFAULT_STATE = (Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) /
                 'TeyvatArchive') if FROZEN else APP / '.local'
ROOT = Path(os.environ.get('TEYVAT_CORPUS', DEFAULT_ROOT)).resolve()
STATE = Path(os.environ.get('TEYVAT_USER_DATA', DEFAULT_STATE)).resolve()
LOCK = APP / 'knowledge.lock.json'
DATA_DIRS = ('10-剧情','20-世界观与设定','30-人物档案','40-文献与书籍',
             '50-图鉴资料','60-活动与版本','70-索引与检索','80-原始归档','90-其他内容','update_20261009')
