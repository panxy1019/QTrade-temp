"""Local fixture only: relay native receive-pack bytes unchanged across one barrier.

No hook, synthetic Git packet, network transport, or production operator integration.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

barrier=Path(sys.argv[1]).resolve();repo=Path(sys.argv[2]).resolve()
assert barrier.is_dir() and repo.is_dir() and barrier.parent==repo.parent
child=subprocess.Popen([shutil.which('git'),'-c','core.hooksPath=/dev/null','receive-pack',str(repo)],
    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=sys.stderr.buffer)
try:
    while True:
        prefix=child.stdout.read(4)
        assert len(prefix)==4
        length=int(prefix,16)
        payload=b'' if length==0 else child.stdout.read(length-4)
        assert length==0 or len(payload)==length-4
        sys.stdout.buffer.write(prefix+payload)
        if length==0:break
    sys.stdout.buffer.flush()
    (barrier/'ADVERTISED').open('xb').close()
    deadline=time.monotonic()+10
    while not (barrier/'RELEASE').exists():
        if time.monotonic()>deadline:raise TimeoutError('local fixture barrier')
        time.sleep(.01)
    def copy_input():
        try:
            while True:
                raw=os.read(sys.stdin.fileno(),8192)
                if not raw:break
                child.stdin.write(raw);child.stdin.flush()
        except (BrokenPipeError,OSError):pass
        finally:
            try:child.stdin.close()
            except OSError:pass
    threading.Thread(target=copy_input,daemon=True).start()
    while True:
        raw=child.stdout.read1(8192)
        if not raw:break
        sys.stdout.buffer.write(raw);sys.stdout.buffer.flush()
    code=child.wait(timeout=10)
except BaseException:
    child.kill();child.wait();code=2
finally:
    sys.stdout.buffer.flush()
os._exit(code)
