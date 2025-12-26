import base64
import hmac
import json
import time
from typing import Dict, Optional, Any
import mod as m
import hashlib

class Auth:
    mod = 'auth.v0'
    def __init__(self, *args, **kwargs ):
        _auth = m.mod(self.mod)( *args, **kwargs )
        for _k in dir(_auth):
            if _k.startswith('__') and _k.endswith('__'):
                continue
            setattr(self, _k, getattr(_auth, _k))
        self._auth = _auth
    
            