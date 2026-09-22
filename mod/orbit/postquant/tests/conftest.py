"""Every test runs against a throwaway chain and keystore.

The data and key directories are read from the environment at import time in
chain.py and keys.py, so they are pointed at a temp dir here, before any test
imports either — a test suite that could touch ~/.mod/postquant would be a
test suite that could spend the operator's PQ.
"""

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix='postquant-test-')
os.environ['POSTQUANT_DATA_DIR'] = _TMP
os.environ['POSTQUANT_KEY_DIR'] = _TMP
os.environ['POSTQUANT_CHAIN_ID'] = 'postquant-test'

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
