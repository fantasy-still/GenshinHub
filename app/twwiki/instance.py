"""One writer per user-data directory. The OS releases the lock after a crash."""
from contextlib import contextmanager
import os

class AlreadyRunning(Exception):pass

@contextmanager
def single_instance(directory):
    directory.mkdir(parents=True,exist_ok=True)
    f=(directory/'instance.lock').open('a+b')
    try:
        if f.tell()==0:f.write(b'0');f.flush()
        f.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as e:raise AlreadyRunning() from e
        yield
    finally:f.close()
