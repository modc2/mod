"""
Who gets to see an address.

A name is public by design — it is what you hand out. The address behind it
is not: for anyone who is not running this box, "what IP does modc2.com sit
on" is reconnaissance, not navigation. So every HTTP and MCP response leaves
through one masking pass: IPv4 literals become `x.x.x.x` and IPv6 literals
become `x:x:x::x` unless the caller's signed token is the deployment owner's.

The mask runs over the serialized JSON, not the object tree, so an address is
caught wherever it appears — a record value, a prose sentence in `check()`,
a step in `plan()`, a tool result nested inside an MCP envelope. Addresses
that say nothing about the box (loopback, the unspecified bind, the
well-known public resolvers) stay visible, because masking `0.0.0.0` teaches
nobody anything and breaks the sentence it sits in.

The owner can turn the whole thing off with `settings.private_ips = false`.
The authoritative listener on UDP/TCP is untouched: a DNS query cannot carry
a token, and a zone actually delegated to this box is answered to the whole
internet by definition. What this hides is the HTTP surface — the one place
an anonymous browser or agent would otherwise read the address off a page.
"""
import ipaddress
import json
import re
import time

import identity
import settings

MASK4 = 'x.x.x.x'
MASK6 = 'x:x:x::x'

# Addresses that identify nothing: binds, loopback, the resolvers everyone
# already knows. Masking these only makes honest sentences unreadable.
ALLOW = {'0.0.0.0', '127.0.0.1', '255.255.255.255',
         '1.1.1.1', '1.0.0.1', '8.8.8.8', '8.8.4.4', '9.9.9.9',
         '::', '::1'}

_V4 = re.compile(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])')
_V6 = re.compile(r'(?<![0-9A-Za-z:.])'
                 r'(?:(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f:]*[0-9A-Fa-f]'
                 r'|::(?:[0-9A-Fa-f]{1,4}:)*[0-9A-Fa-f]{1,4})'
                 r'(?![0-9A-Za-z:.])')


def _mask4(m):
    s = m.group(0)
    try:
        ipaddress.IPv4Address(s)
    except ValueError:
        return s                      # 999.1.2.3, a version string, etc.
    return s if s in ALLOW else MASK4


def _mask6(m):
    s = m.group(0)
    try:
        ipaddress.IPv6Address(s)
    except ValueError:
        return s                      # a timestamp-shaped near miss
    return s if s in ALLOW else MASK6


def mask_text(text):
    return _V6.sub(_mask6, _V4.sub(_mask4, text))


def on():
    return bool(settings.get('private_ips', True))


# Verifying a token is not free, and the console sends the same one with
# every request — remember the verdict for a minute instead of re-checking.
_seen = {}


def _address(token):
    tok = identity.strip(token)
    if not tok:
        return identity.whoami(None)  # open mode collapses to one identity
    hit = _seen.get(tok)
    now = time.time()
    if hit and now - hit[1] < 60:
        return hit[0]
    addr = identity.whoami(tok)
    if len(_seen) > 256:
        _seen.clear()
    _seen[tok] = (addr, now)
    return addr


def exempt(token):
    """The deployment owner (and open mode) sees the real addresses."""
    return identity.role(_address(token)) == identity.OWNER


def body(payload, token):
    """A JSON body for this caller: addresses masked unless they own the box."""
    text = json.dumps(payload, indent=2, default=str)
    if on() and not exempt(token):
        text = mask_text(text)
    return text.encode()
