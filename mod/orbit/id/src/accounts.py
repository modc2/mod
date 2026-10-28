"""Accounts that cannot sign: GitHub, X, a domain, a page you control.

A wallet proves itself with a key, and that proof stands on its own forever. A
GitHub account cannot do that — there is no key to sign with — so the only thing
available is publication: put a one-line token somewhere only the account holder
can write, and let this module go and read it.

That is a genuinely weaker proof, and it is labelled as such everywhere it
appears (`strength: publication` against `strength: key`). Two differences
matter, and the console says both out loud:

  it can be undone   — delete the gist, drop the DNS record, and the evidence is
                       gone. `recheck()` exists for exactly this reason.
  it is a snapshot   — it proves control at the moment of fetching, and no
                       later. A key proof can be re-verified offline in ten
                       years; a publication proof can only be re-fetched.

Nothing here is trusted blindly: the fetch is capped in size and time, it will
not follow a redirect off the origin it started on, and the account name is
taken from the *service's own* answer (the gist's owner login, the tweet's
screen name) rather than from whatever the user typed.
"""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

MAX_BYTES = 512 * 1024
TIMEOUT = 8
AGENT = 'mod-id/1.0 (+identity proof checker)'


class ProofError(ValueError):
    """The token was not found where it was supposed to be."""


class AccountError(ValueError):
    """Not a usable handle for this service."""


def fetch(url: str, headers: Optional[Dict[str, str]] = None,
          as_json: bool = False) -> Any:
    """A small, cautious GET. Anything unusual becomes a readable error."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ('https', 'http'):
        raise ProofError(f'{parsed.scheme or "that"} is not a scheme this will fetch')
    request = urllib.request.Request(url, headers={'User-Agent': AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            if urllib.parse.urlparse(response.geturl()).netloc != parsed.netloc:
                raise ProofError(f'{url} redirected off {parsed.netloc} — refused')
            body = response.read(MAX_BYTES)
    except urllib.error.HTTPError as exc:
        raise ProofError(f'{url} answered {exc.code} {exc.reason}') from exc
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        raise ProofError(f'could not reach {parsed.netloc}: {exc}') from exc
    text = body.decode('utf-8', 'replace')
    if as_json:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProofError(f'{parsed.netloc} did not answer with JSON: {exc}') from exc
    return text


@dataclass
class Service:
    name: str
    title: str
    where: str                         # where the holder is told to put the token
    parse: Callable[[str], str]
    check: Callable[..., Dict[str, Any]]
    hint: str = ''
    aliases: List[str] = field(default_factory=list)

    def card(self) -> Dict[str, Any]:
        return {'service': self.name, 'title': self.title, 'strength': 'publication',
                'where': self.where, 'hint': self.hint, 'aliases': self.aliases}


SERVICES: Dict[str, Service] = {}
_ALIASES: Dict[str, str] = {}


def register(service: Service) -> Service:
    SERVICES[service.name] = service
    _ALIASES[service.name] = service.name
    for alias in service.aliases:
        _ALIASES[alias] = service.name
    return service


def get(name: str) -> Service:
    key = (name or '').strip().lower()
    if key not in _ALIASES:
        raise AccountError(f'unknown service {name!r} — one of: {", ".join(sorted(SERVICES))}')
    return SERVICES[_ALIASES[key]]


def known() -> List[Dict[str, Any]]:
    return [service.card() for service in SERVICES.values()]


def is_service(name: str) -> bool:
    return (name or '').strip().lower() in _ALIASES


# ── github ───────────────────────────────────────────────────────────────

_HANDLE = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$')


def _github_parse(handle: str) -> str:
    text = handle.strip().lstrip('@')
    if text.startswith('http'):
        text = urllib.parse.urlparse(text).path.strip('/').split('/')[0]
    if not _HANDLE.match(text):
        raise AccountError(f'{handle!r} is not a GitHub login')
    return text.lower()


def _github_check(handle: str, token: str, source: str = None, **_: Any) -> Dict[str, Any]:
    want = _github_parse(handle)
    gists = fetch(f'https://api.github.com/users/{want}/gists?per_page=20',
                  headers={'Accept': 'application/vnd.github+json'}, as_json=True)
    if not isinstance(gists, list):
        raise ProofError(f'GitHub did not list gists for {want}')
    for gist in gists:
        owner = ((gist.get('owner') or {}).get('login') or '').lower()
        if owner != want:
            continue
        for meta in (gist.get('files') or {}).values():
            raw = meta.get('raw_url')
            if not raw:
                continue
            try:
                body = fetch(raw)
            except ProofError:
                continue
            if token in body:
                return {'account': f'github:{want}', 'display': f'@{want}',
                        'source': gist.get('html_url') or raw,
                        'detail': f'token found in a public gist owned by {want}'}
    raise ProofError(
        f'no public gist of {want}\'s contains the token. Create a public gist '
        'whose contents include the line, then submit again — GitHub can take a '
        'few seconds to list it.')


register(Service(
    name='github', title='GitHub', aliases=['gh'],
    where='a public gist on your account',
    parse=_github_parse, check=_github_check,
    hint='gist.github.com → new public gist → paste the token → Create'))


# ── x / twitter ──────────────────────────────────────────────────────────

def _x_parse(handle: str) -> str:
    text = handle.strip().lstrip('@')
    if text.startswith('http'):
        parts = urllib.parse.urlparse(text).path.strip('/').split('/')
        text = parts[0] if parts else ''
    if not re.match(r'^[A-Za-z0-9_]{1,15}$', text):
        raise AccountError(f'{handle!r} is not an X handle')
    return text.lower()


def _x_check(handle: str, token: str, source: str = None, **_: Any) -> Dict[str, Any]:
    want = _x_parse(handle)
    if not source:
        raise ProofError('post the token, then submit the URL of that post as `source`')
    found = re.search(r'/status/(\d+)', source)
    if not found:
        raise ProofError(f'{source} is not a link to a post')
    data = fetch('https://cdn.syndication.twimg.com/tweet-result'
                 f'?id={found.group(1)}&token=a&lang=en', as_json=True)
    text = data.get('text') or ''
    author = ((data.get('user') or {}).get('screen_name') or '').lower()
    if token not in text:
        raise ProofError('that post does not contain the token')
    if author != want:
        raise ProofError(f'that post is by @{author}, not @{want}')
    return {'account': f'x:{want}', 'display': f'@{want}', 'source': source,
            'detail': 'token found in a public post by this handle',
            'caveat': 'read through X\'s public embed endpoint, which is '
                      'undocumented and can stop answering without notice'}


register(Service(
    name='x', title='X (Twitter)', aliases=['twitter'],
    where='a public post from the account',
    parse=_x_parse, check=_x_check,
    hint='post the token, then paste the link to the post'))


# ── dns ──────────────────────────────────────────────────────────────────

_DOMAIN = re.compile(r'^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$')


def _dns_parse(domain: str) -> str:
    text = domain.strip().lower().rstrip('.')
    if text.startswith('http'):
        text = urllib.parse.urlparse(text).netloc
    text = text.split('@')[-1].split(':')[0]
    if not _DOMAIN.match(text):
        raise AccountError(f'{domain!r} is not a domain name')
    return text


def _txt_records(name: str) -> List[str]:
    data = fetch(f'https://cloudflare-dns.com/dns-query?name={name}&type=TXT',
                 headers={'Accept': 'application/dns-json'}, as_json=True)
    return [entry.get('data', '').strip('"').replace('" "', '')
            for entry in (data.get('Answer') or []) if entry.get('type') == 16]


def _dns_check(handle: str, token: str, source: str = None, **_: Any) -> Dict[str, Any]:
    want = _dns_parse(handle)
    checked = []
    for name in (f'_mod-id.{want}', want):
        checked.append(name)
        try:
            records = _txt_records(name)
        except ProofError:
            continue
        for record in records:
            if token in record:
                return {'account': f'dns:{want}', 'display': want, 'source': f'TXT {name}',
                        'detail': f'token found in the TXT record of {name}'}
    raise ProofError(
        f'no TXT record containing the token on {" or ".join(checked)}. '
        f'Add: _mod-id.{want} TXT "<token>" — and allow for the TTL.')


register(Service(
    name='dns', title='A domain name', aliases=['domain'],
    where='a TXT record at _mod-id.<domain>',
    parse=_dns_parse, check=_dns_check,
    hint='_mod-id.example.com  TXT  "mod:id/v1 …" — resolved over DoH, so the '
         'host running this needs no resolver of its own'))


# ── any page you control ─────────────────────────────────────────────────

def _web_parse(url: str) -> str:
    text = url.strip()
    if not text.startswith(('http://', 'https://')):
        text = 'https://' + text
    parsed = urllib.parse.urlparse(text)
    if not parsed.netloc:
        raise AccountError(f'{url!r} is not a URL')
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc.lower(), parsed.path or '/', '', parsed.query, ''))


def _web_check(handle: str, token: str, source: str = None, **_: Any) -> Dict[str, Any]:
    want = _web_parse(handle)
    body = fetch(want)
    if token not in body:
        raise ProofError(f'{want} does not contain the token '
                         f'(read {len(body)} bytes)')
    return {'account': f'web:{want}', 'display': want, 'source': want,
            'detail': 'token served by this exact URL',
            'caveat': 'proves control of this page, not of the whole domain — '
                      'on a shared host, a page is not the site'}


register(Service(
    name='web', title='A page you control', aliases=['url', 'http'],
    where='anywhere in the body of a URL you can publish to',
    parse=_web_parse, check=_web_check,
    hint='a README on a personal site, a pinned file, a keybase-style proof page'))


# ── the surface the rest of the module uses ──────────────────────────────

def parse(service: str, handle: str) -> str:
    return get(service).parse(handle)


def verify(service: str, handle: str, token: str, source: str = None) -> Dict[str, Any]:
    entry = get(service)
    result = entry.check(handle, token, source=source)
    result.update({'ok': True, 'service': entry.name, 'strength': 'publication'})
    return result
