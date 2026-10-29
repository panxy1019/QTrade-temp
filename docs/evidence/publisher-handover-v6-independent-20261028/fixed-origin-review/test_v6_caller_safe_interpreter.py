"""Reject optimized interpreter before any helper/manifest/production access."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

@pytest.mark.parametrize('mode',['prepare','advance','observe'])
def test_optimized_caller_fails_before_loading_any_remote_path(mode):
    path=Path(__file__).with_name('publisher_v6_control_call_20261028.py')
    completed=subprocess.run([sys.executable,'-B','-O',str(path),mode],
        env={**os.environ,'LIVE_TRADING_ENABLED':'false'},capture_output=True,text=True)
    assert completed.returncode!=0
    assert not completed.stdout
    assert 'PUBLISHER_CONTROL_SAFE_INTERPRETER_AND_LIVE_OFF_REQUIRED' in completed.stderr
    assert 'HELPER_HASH' not in completed.stderr and 'FileNotFoundError' not in completed.stderr
