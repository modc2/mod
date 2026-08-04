"""
encrypt api — bring-your-own-circuit encrypted messages, stored in the store mod.

Auth
    Same protocol token as the rest of the fleet: a wallet-signed
    `{data, time, key, signature}` envelope in `Authorization: Bearer <token>`,
    verified statelessly by `m.mod('auth')`. The token is *forwarded* to the
    store gateway on every upload/fetch/delete, so the caller — not this module —
    owns every object, and the store's whitelist, quota and terms apply as usual.

Keys
    Passphrases arrive in request bodies, are handed to the sandboxed circuit
    process, and are dropped when the request ends. Nothing writes them: not the
    message index, not the logs, not the store.

Endpoints
    GET    /health                     liveness + sandbox capabilities
    GET    /status                     store reachability, isolation, counts
    GET    /me                         caller address + store identity

    GET    /circuits                   my circuits + public ones
    POST   /circuits                   register a circuit from JSON {source,name}
    POST   /circuits/upload            register a circuit from a .py upload
    POST   /circuits/install           install a shared circuit by store CID
    GET    /circuits/{id}              one circuit's metadata
    GET    /circuits/{id}/source       download the circuit source
    DELETE /circuits/{id}[?force=1]    delete a circuit server-side

    GET    /messages                   my messages (metadata only)
    POST   /messages                   encrypt + store  {circuit,key,text|data_b64}
    POST   /messages/attach            register a client-encrypted blob by CID
    GET    /messages/{id}              one message's metadata
    GET    /messages/{id}/download     raw ciphertext (?burn=1 deletes after)
    POST   /messages/{id}/open         decrypt server-side  {key}
    POST   /messages/{id}/publish      flip the ciphertext public/private
    DELETE /messages/{id}              delete server-side (store object + row)
    DELETE /messages?confirm=true      purge everything I own

Run (see mod.py serve / `m encrypt/serve`):
    uvicorn api:app --host 0.0.0.0 --port 50380 --app-dir <module>/api
"""
import json
import os
import sys
from pathlib import Path
from typing import Optional

# `mod` first: this module ships its own mod.py anchor, and putting the module
# dir on sys.path before the package is imported would shadow the real package.
import mod as m  # noqa: E402

MODULE_DIR = Path(__file__).resolve().parent.parent
if str(MODULE_DIR) not in sys.path:
    sys.path.append(str(MODULE_DIR))

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Query, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import Response  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from encryptor import AccessDenied, CircuitError, Engine, NotFound, StoreError  # noqa: E402

CONFIG = json.loads((MODULE_DIR / 'config.json').read_text())
SESSION_TTL = int(os.environ.get('ENCRYPT_SESSION_TTL', 604800))
AUTH = m.mod('auth')(crypto_type='ecdsa', max_age=SESSION_TTL)
ENGINE = Engine(CONFIG)

app = FastAPI(title='encrypt', description=CONFIG.get('description', 'encrypt'))
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'],
                   allow_headers=['*'], allow_credentials=False)


# ── auth ─────────────────────────────────────────────────────────────

def bearer(authorization: Optional[str]) -> str:
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'missing bearer token')
    return authorization[7:].strip()


def caller(authorization: Optional[str]) -> tuple:
    """(address, token) — the address for our own ownership checks, the token
    for the store, which re-verifies it itself."""
    token = bearer(authorization)
    try:
        headers = AUTH.verify(token)
    except Exception as e:
        raise HTTPException(401, f'invalid or expired token: {e}')
    address = str(headers.get('key', '')).lower()
    if not address.startswith('0x'):
        raise HTTPException(401, 'token missing signer address')
    return address, token


# ── errors ───────────────────────────────────────────────────────────

@app.exception_handler(NotFound)
def _not_found(request, exc):
    return _error(404, str(exc))


@app.exception_handler(AccessDenied)
def _denied(request, exc):
    return _error(403, str(exc))


@app.exception_handler(CircuitError)
def _circuit_failed(request, exc):
    # The circuit is user code; its failure is a 400 with its own words, not a 500.
    return _error(400, str(exc))


@app.exception_handler(StoreError)
def _store_failed(request, exc):
    status = exc.status if exc.status in (401, 403, 404, 413, 451) else 502
    return _error(status, str(exc))


def _error(status: int, detail: str) -> Response:
    return Response(json.dumps({'detail': detail}), status_code=status,
                    media_type='application/json')


# ── module ───────────────────────────────────────────────────────────

@app.get('/health')
def health():
    return {'ok': True, 'service': 'encrypt', 'sandbox': ENGINE.capabilities()}


@app.get('/status')
def status(authorization: Optional[str] = Header(default=None)):
    token = None
    if authorization and authorization.startswith('Bearer '):
        token = authorization[7:].strip()
    return ENGINE.status(token)


@app.get('/me')
def me(authorization: Optional[str] = Header(default=None)):
    address, token = caller(authorization)
    out = {'address': address}
    try:
        out['store'] = ENGINE.store.me(token)
    except StoreError as e:
        out['store'] = {'error': str(e)}
    return out


# ── circuits ─────────────────────────────────────────────────────────

class CircuitBody(BaseModel):
    source: str
    name: Optional[str] = None
    public: bool = False
    pin: bool = True


class InstallBody(BaseModel):
    cid: str
    name: Optional[str] = None
    public: bool = False


@app.get('/circuits')
def circuits(authorization: Optional[str] = Header(default=None)):
    address, _ = caller(authorization)
    return {'circuits': ENGINE.circuits(address)}


@app.post('/circuits')
def circuit_add(body: CircuitBody, authorization: Optional[str] = Header(default=None)):
    address, token = caller(authorization)
    return ENGINE.add_circuit(token, body.source.encode(), name=body.name or 'circuit',
                              owner=address, public=body.public, pin=body.pin)


@app.post('/circuits/upload')
async def circuit_upload(file: UploadFile = File(...),
                         name: Optional[str] = Form(None),
                         public: str = Form('false'),
                         pin: str = Form('true'),
                         authorization: Optional[str] = Header(default=None)):
    address, token = caller(authorization)
    source = await file.read()
    label = name or Path(file.filename or 'circuit.py').stem
    return ENGINE.add_circuit(token, source, name=label, owner=address,
                              public=_truthy(public), pin=_truthy(pin))


@app.post('/circuits/install')
def circuit_install(body: InstallBody, authorization: Optional[str] = Header(default=None)):
    address, token = caller(authorization)
    return ENGINE.install_circuit(token, body.cid, owner=address, name=body.name,
                                  public=body.public)


@app.get('/circuits/{cid}')
def circuit_get(cid: str, authorization: Optional[str] = Header(default=None)):
    address, _ = caller(authorization)
    return ENGINE.circuit(cid, address)


@app.get('/circuits/{cid}/source')
def circuit_source(cid: str, authorization: Optional[str] = Header(default=None)):
    address, _ = caller(authorization)
    row = ENGINE.circuit(cid, address)
    return Response(ENGINE.circuit_source(cid, address), media_type='text/x-python',
                    headers={'content-disposition': f'attachment; filename="{row["name"]}.py"'})


@app.delete('/circuits/{cid}')
def circuit_rm(cid: str, force: bool = False,
               authorization: Optional[str] = Header(default=None)):
    address, token = caller(authorization)
    return ENGINE.rm_circuit(token, cid, address, force=force)


# ── messages ─────────────────────────────────────────────────────────

class MessageBody(BaseModel):
    circuit: str
    key: Optional[str] = None
    key_b64: Optional[str] = None
    text: Optional[str] = None
    data_b64: Optional[str] = None
    label: Optional[str] = None
    public: bool = False
    burn: bool = False
    params: Optional[dict] = None


class AttachBody(BaseModel):
    cid: str
    circuit: Optional[str] = None
    label: Optional[str] = None
    burn: bool = False
    params: Optional[dict] = None


class OpenBody(BaseModel):
    key: Optional[str] = None
    key_b64: Optional[str] = None


class PublishBody(BaseModel):
    public: bool = True


@app.get('/messages')
def messages(authorization: Optional[str] = Header(default=None)):
    address, _ = caller(authorization)
    return {'messages': ENGINE.messages(address)}


@app.post('/messages')
def message_create(body: MessageBody, authorization: Optional[str] = Header(default=None)):
    import base64
    address, token = caller(authorization)
    if body.data_b64 is not None:
        data = base64.b64decode(body.data_b64)
    elif body.text is not None:
        data = body.text.encode()
    else:
        raise HTTPException(400, 'nothing to encrypt: send text or data_b64')
    return ENGINE.encrypt(token, address, circuit=body.circuit, data=data,
                          key=body.key, key_b64=body.key_b64, label=body.label,
                          public=body.public, burn=body.burn, params=body.params)


@app.post('/messages/attach')
def message_attach(body: AttachBody, authorization: Optional[str] = Header(default=None)):
    address, token = caller(authorization)
    return ENGINE.attach(token, address, cid=body.cid, circuit=body.circuit,
                         label=body.label, burn=body.burn, params=body.params)


@app.delete('/messages')
def messages_purge(confirm: bool = Query(False),
                   authorization: Optional[str] = Header(default=None)):
    address, token = caller(authorization)
    if not confirm:
        raise HTTPException(400, 'add ?confirm=true — this deletes every message you own')
    return ENGINE.purge(token, address)


@app.get('/messages/{mid}')
def message_get(mid: str, authorization: Optional[str] = Header(default=None)):
    address, _ = caller(authorization)
    return ENGINE.message(mid, address)


@app.get('/messages/{mid}/download')
def message_download(mid: str, burn: bool = False,
                     authorization: Optional[str] = Header(default=None)):
    address, token = caller(authorization)
    data, row = ENGINE.ciphertext(token, address, mid, burn=burn)
    name = f'{row.get("label") or mid}.enc'
    return Response(data, media_type='application/octet-stream',
                    headers={'content-disposition': f'attachment; filename="{name}"',
                             'x-encrypt-circuit': str(row.get('circuit') or ''),
                             'x-encrypt-cid': row.get('cid', '')})


@app.post('/messages/{mid}/open')
def message_open(mid: str, body: OpenBody = Body(default=OpenBody()),
                 authorization: Optional[str] = Header(default=None)):
    address, token = caller(authorization)
    return ENGINE.open(token, address, mid, key=body.key, key_b64=body.key_b64)


@app.post('/messages/{mid}/publish')
def message_publish(mid: str, body: PublishBody = Body(default=PublishBody()),
                    authorization: Optional[str] = Header(default=None)):
    address, token = caller(authorization)
    return ENGINE.publish(token, address, mid, body.public)


@app.delete('/messages/{mid}')
def message_rm(mid: str, authorization: Optional[str] = Header(default=None)):
    address, token = caller(authorization)
    return ENGINE.delete(token, address, mid)


def _truthy(v) -> bool:
    return str(v).strip().lower() in ('1', 'true', 'yes', 'on')
