import os,signal,sys,threading
from pathlib import Path
stop=threading.Event()
signal.signal(signal.SIGTERM,lambda *args:stop.set())
Path(sys.argv[1]).write_text(str(os.getpid()))
stop.wait(180)
