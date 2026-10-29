import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import time
import pytest

@pytest.fixture
def driver():
    p=Path(__file__).with_name('profile_publication_pack_cache_cold_20261028.py')
    s=importlib.util.spec_from_file_location('cold_profile_stream_test',p)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def cleanup(child):
    from scripts.publish_research_snapshot import _cleanup_git_group
    _cleanup_git_group(child)

def test_real_bidirectional_pipe_handles_input_larger_than_buffer(driver,tmp_path):
    raw=b'SYNTHETIC_PUBLIC_DATA'*100000;p=tmp_path/'echo.pack'
    r=driver.stream_pack([sys.executable,'-B','-P','-c','import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())'],
        os.environ.copy(),raw,p,time.monotonic()+10,cleanup)
    assert p.read_bytes()==raw and r['sha256']==hashlib.sha256(raw).hexdigest()

def test_output_cap_terminates_before_writing_over_budget(driver,tmp_path,monkeypatch):
    monkeypatch.setattr(driver,'LIMIT',1024);p=tmp_path/'too-large.pack'
    with pytest.raises(ValueError,match='PACK_BYTE_LIMIT'):
        driver.stream_pack([sys.executable,'-B','-P','-c','import sys;sys.stdout.buffer.write(b"x"*100000)'],
            os.environ.copy(),b'',p,time.monotonic()+5,cleanup)
    assert p.stat().st_size<=1024

def test_stdin_backpressure_does_not_bypass_deadline(driver,tmp_path):
    before=time.monotonic()
    with pytest.raises(ValueError,match='STAGING_DEADLINE'):
        driver.stream_pack([sys.executable,'-B','-P','-c','import time;time.sleep(20)'],
            os.environ.copy(),b'x'*2000000,tmp_path/'blocked.pack',before+.2,cleanup)
    assert time.monotonic()-before<5

def test_nonzero_cannot_produce_success(driver,tmp_path):
    with pytest.raises(ValueError,match='PACK_COMMAND_FAILED'):
        driver.stream_pack([sys.executable,'-B','-P','-c','raise SystemExit(2)'],
            os.environ.copy(),b'',tmp_path/'bad.pack',time.monotonic()+5,cleanup)
