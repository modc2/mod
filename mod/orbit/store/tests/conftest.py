"""
Point the module's state at a throwaway directory BEFORE anything imports it.

`src.library` resolves STORE_SHARE_HOME at import time, so this has to happen
during collection — a fixture would be too late and the suite would run against
`~/.mod/store-share`, which is somebody's actual pictures.
"""
import os
import sys
import tempfile
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_DIR))

_TMP = tempfile.mkdtemp(prefix='store-test-')
os.environ['STORE_SHARE_HOME'] = _TMP
os.environ['STORE_SHARE_QUIET'] = '1'

import pytest  # noqa: E402

from src import library  # noqa: E402


@pytest.fixture(autouse=True)
def clean():
    """Every test starts with an empty library."""
    conn = library.connect()
    conn.execute('DELETE FROM images')
    conn.execute('DELETE FROM grants')
    conn.close()
    yield
