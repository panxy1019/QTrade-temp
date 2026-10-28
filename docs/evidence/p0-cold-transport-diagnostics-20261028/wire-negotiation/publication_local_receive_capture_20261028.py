"""Local fixture-only stdin tee around mature native Git receive-pack."""
import os
from pathlib import Path
import subprocess
import sys
import threading


def main():
    assert len(sys.argv)==4
    capture,git,repo=map(Path,sys.argv[1:])
    assert capture.is_absolute() and git.is_absolute() and repo.is_absolute()
    assert repo.resolve()==repo and repo==capture.parent/'remote.git'
    assert capture.name=='wire-input.bin' and not capture.exists()
    assert (repo/'synthetic-local-fixture-only').read_bytes()==b'NO_NETWORK_OR_PRODUCTION\n'
    descriptor=os.open(capture,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0),0o600)
    child=subprocess.Popen([str(git),'-c','receive.unpackLimit=0',
        '-c','core.hooksPath='+os.devnull,'receive-pack',str(repo)],stdin=subprocess.PIPE)
    def copy():
        while True:
            raw=os.read(sys.stdin.fileno(),65536)
            if not raw:break
            for fd in (descriptor,child.stdin.fileno()):
                remaining=memoryview(raw)
                while remaining:
                    count=os.write(fd,remaining)
                    remaining=remaining[count:]
        child.stdin.close()
    # Child emits native advertisement/ACK directly through inherited stdout.
    # The daemon uses unbuffered OS calls so it cannot retain Python I/O locks
    # if the native sender leaves stdin open after receive-pack has exited.
    threading.Thread(target=copy,daemon=True).start()
    code=child.wait()
    os.fsync(descriptor);os.close(descriptor)
    os._exit(code)


if __name__=='__main__':main()
