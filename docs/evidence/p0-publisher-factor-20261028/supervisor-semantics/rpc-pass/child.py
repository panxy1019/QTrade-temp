
import json,os,signal,subprocess,sys,threading,time
from pathlib import Path
root=Path(sys.argv[1]); name=sys.argv[2]; release=sys.argv[3]; scenario=sys.argv[4]
stop=threading.Event()
def handle(signum,frame):
    stop.set()
signal.signal(signal.SIGTERM,handle)
def tick(pid):
    return Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()[19]
def record(event,**values):
    value={'event':event,'pid':os.getpid(),'ppid':os.getppid(),'start_ticks':tick(os.getpid()),
           'name':name,'release':release,'monotonic':time.monotonic(),**values}
    with (root/'child.events.jsonl').open('a') as stream:
        stream.write(json.dumps(value,sort_keys=True)+'\n');stream.flush();os.fsync(stream.fileno())
    print(json.dumps(value,sort_keys=True),flush=True)
counter=root/(name+'.counter')
generation=int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(generation))
record('START',generation=generation)
worker=None;ingroup=None
try:
    if name!='controlled' or generation>=2:
        if name=='controlled' and generation>2: record('UNEXPECTED_EXTRA_GENERATION',generation=generation)
        while not stop.wait(.1): pass
    else:
        worker=subprocess.Popen(['/usr/bin/python3','-c','import time;time.sleep(600)'],
                                cwd=root,start_new_session=True)
        record('OWNED_WORKER_START',worker_pid=worker.pid,worker_start_ticks=tick(worker.pid),
               worker_session=os.getsid(worker.pid))
        ingroup=subprocess.Popen(['/usr/bin/python3','-c','import time;time.sleep(600)'],cwd=root)
        record('INGROUP_HELPER_START',ingroup_pid=ingroup.pid,ingroup_start_ticks=tick(ingroup.pid),
               helper_pgid=os.getpgid(ingroup.pid),main_pgid=os.getpgrp())
        assert os.getpgid(ingroup.pid)==os.getpgrp()
        if scenario=='normal':
            deadline=time.monotonic()+2
            while time.monotonic()<deadline: time.sleep(.05)
            record('NORMAL_PHASE_COMPLETE')
        else:
            while not stop.wait(.05): pass
            record('TERM_DRAIN_BEGIN')
            deadline=time.monotonic()+112.0
            while time.monotonic()<deadline and not (root/'fixture-cleanup-abort').exists():
                time.sleep(.1)
            record('TERM_DRAIN_COMPLETE')
finally:
    if worker is not None and worker.poll() is None:
        worker.terminate() # Parent-held, unreaped fixture child; no production PID fallback.
        code=worker.wait(timeout=5)
        record('OWNED_WORKER_CLEANED',worker_pid=worker.pid,worker_returncode=code)
    if ingroup is not None:
        assert ingroup.poll() is None,'SAME_GROUP_HELPER_RECEIVED_PREMATURE_SIGNAL'
        ingroup.terminate()
        code=ingroup.wait(timeout=5)
        record('INGROUP_HELPER_CLEANED',ingroup_pid=ingroup.pid,helper_returncode=code)
record('NATURAL_EXIT_ZERO',generation=generation)
sys.exit(0)
