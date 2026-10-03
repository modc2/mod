"""
Local REST surface for the zcash module, used by the web app and by agents.

Two surfaces exist and they are deliberately different:

  * the mod-protocol server (port 50148) is the fleet's front door and is
    gated by the shared gate -- every function there needs owner auth;
  * this server is bound to localhost and backs the app. Reads are open,
    but anything that can move funds or expose a seed needs a bearer token
    read from ~/.mod/zcash/server.secret (generated on first start).

Three shapes of the same module live here, over one port:

    POST /<fn>       the module's functions by name, as the app calls them
    POST /mcp        the same functions as MCP tools (JSON-RPC 2.0)
    GET  /mcp        the tool schema, without needing an MCP client to see it

The MCP tools are mounted from mcp.py and run the same Mod instance these
routes do, so a tool and a route cannot answer the same question differently.
The bearer gate covers both: GUARDED_FNS names the functions, and a tool that
reaches one of them is gated too -- gating the REST routes alone would be no
gate at all, because /mcp reaches every function by name.

Print the token with:  m zcash/token   or   cat ~/.mod/zcash/server.secret
"""

import os
import secrets
import sys

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

_root = os.path.dirname(os.path.abspath(__file__))
for p in (_root, os.path.join(_root, 'zcash')):
    if p not in sys.path:
        sys.path.insert(0, p)

import mcp as mcp_server  # noqa: E402  -- this module's mcp.py; _root leads sys.path

app = FastAPI(title="Zcash", version="2.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.environ.get(
        "ZCASH_CORS_ORIGINS",
        "http://localhost:50149,http://127.0.0.1:50149").split(",") if o],
    allow_methods=["*"], allow_headers=["*"],
)

# Functions that only read public chain data or local metadata.
OPEN_FNS = {
    'info', 'block', 'tx', 'address', 'mempool', 'price', 'network', 'search',
    'validate', 'estimate_fee', 'capabilities', 'status', 'test',
    'bridge_chains', 'bridge_quote', 'bridge_status', 'bridge_maya',
    # Building the EVM transaction that funds a deposit address reveals
    # nothing and signs nothing -- the browser wallet holds those keys.
    'bridge_payment', 'bridge_networks',
    # Teaching and the agent are public: someone who does not yet know what a
    # z-address is has no token, and gating the explanation behind the thing
    # it explains is how people end up guessing instead.
    'learn', 'explain', 'ask', 'agent_status',
    # Quoting a shielded bridge reserves nothing and reveals nothing the
    # caller did not already type. Reserving one does -- see bridge_start.
    'bridge_shielded_plan', 'bridge_shielded_address',
    'wallet_list', 'wallet_info', 'wallet_balance', 'wallet_utxos',
    # A shielded address is public; what has been paid to it is not.
    'shielded_address',
    # Whether this host has a prover installed is a property of the host, not
    # of anyone's money -- and the app has to know it to render the tab.
    'shielded_backend',
}

# Functions that spend, reserve, or reveal secrets.
GUARDED_FNS = {
    'wallet_create', 'wallet_restore', 'wallet_new_address', 'wallet_import',
    'wallet_reveal', 'wallet_delete', 'wallet_label',
    'send', 'broadcast_raw', 'bridge_start', 'bridge_send',
    # bridge_shielded_in reserves a deposit address when reserve=True, and
    # reads a wallet's shielded account when given a name; bridge_shielded_out
    # spends notes. Both are gated whole rather than by argument, because a
    # gate that depends on a flag in the body is a gate someone will flip.
    'bridge_shielded_in', 'bridge_shielded_out',
    # Viewing keys reveal every shielded payment an account ever received,
    # so reading the shielded pool is guarded even though it cannot spend.
    'shielded_new_address', 'shielded_upgrade', 'shielded_export',
    'shielded_scan', 'shielded_balance', 'shielded_scan_tx',
    'shielded_send', 'shielded_node_import', 'shielded_operation',
    # The light-client prover: building it spends CPU, syncing it reveals
    # which wallet is being watched, and spendable/shield touch the notes.
    # Only `shielded_backend` (is a prover present at all) stays open, so the
    # app can show the install button before anyone has a token.
    'shielded_backend_install', 'shielded_sync_start', 'shielded_sync_status',
    'shielded_sync_stop', 'shielded_spendable', 'shielded_shield',
}

# One Mod instance, one token file, shared with the MCP tools -- two loadings
# of zcash/mod.py in one process would mean two caches and two answers.
get_mod = mcp_server.get_mod
secret_path = mcp_server.secret_path
server_token = mcp_server.server_token


def check_auth(fn: str, authorization: str = None):
    if fn not in GUARDED_FNS:
        return
    expected = server_token()
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail=f"{fn} can move funds or reveal secrets and needs the module "
                   f"token. Read it from {secret_path()} and send it as "
                   f"'Authorization: Bearer <token>'.")


@app.get("/")
def index(request: Request):
    """What is served here, in one document."""
    return {
        "module": "zcash",
        "version": app.version,
        "what": "Zcash explorer, HD wallet with real transparent sends, real "
                "Sapling/Orchard shielded addresses, shielded sends proved "
                "locally by a built-in light client, and a bridge to 30+ chains",
        "surfaces": {
            "POST /<fn>": "any module function by name, arguments as a JSON body",
            "GET /fns": "which functions are open and which need the token",
            "POST /mcp": "MCP tools over JSON-RPC 2.0 (Streamable HTTP)",
            "GET /mcp": "the MCP schema: every tool, its arguments and its gate",
            "GET /mcp/tools": "the tool list alone",
            "GET /mcp/config": "copy-paste client config for this endpoint",
            "GET /health": "liveness",
        },
        "mcp": {
            "endpoint": "POST /mcp",
            "url": _mcp_url(request),
            "transport": "Streamable HTTP (JSON-RPC 2.0)",
            "stdio": f"python3 {os.path.join(_root, 'mcp.py')}",
            "tools": len(mcp_server.TOOLS),
            "open_tools": len(mcp_server.OPEN_TOOLS),
        },
        "auth": f"reads are open; spending, wallet secrets and shielded reads need "
                f"'Authorization: Bearer <token>' from {secret_path()} "
                f"(print it with `m zcash/token`)",
    }


@app.get("/health")
def health():
    return {"status": "ok", "module": "zcash", "tools": len(mcp_server.TOOLS)}


@app.get("/fns")
def fns():
    """The function-level gate, and which MCP tool reaches each function."""
    by_fn = {}
    for name, tool in mcp_server.TOOLS.items():
        for fn in tool['fns']:
            by_fn.setdefault(fn, []).append(name)
    return {"open": sorted(OPEN_FNS), "guarded": sorted(GUARDED_FNS),
            "tools": by_fn}


# ── MCP ────────────────────────────────────────────────────────────────────
#
# Declared before the POST /{fn} catch-all: FastAPI matches in registration
# order, and /mcp is not a module function.

def _mcp_url(request: Request) -> str:
    """The endpoint URL as the *caller* reached it.

    A client handed `http://127.0.0.1:8930/mcp` when it actually came through
    the app at `https://host/zcash/api/mcp` would be told to talk to a port it
    cannot see. The app's route handler forwards the prefix and host it served,
    so rebuild the public URL from those when they are there.
    """
    h = request.headers
    scheme = h.get('x-forwarded-proto') or request.url.scheme
    host = h.get('x-forwarded-host') or h.get('host') or request.url.netloc
    prefix = (h.get('x-forwarded-prefix') or '').rstrip('/')
    return f"{scheme}://{host}{prefix}/mcp"


def _mcp_ctx(authorization: str = None) -> "mcp_server.Ctx":
    """An HTTP caller is never local, however loopback the socket is: the app
    proxies public traffic to this port, so trusting the peer address would
    hand the internet the wallet."""
    return mcp_server.Ctx(token=authorization, local=False)


@app.get("/mcp")
def mcp_schema(request: Request):
    """Every tool, its arguments, its gate and a client config -- readable
    without an MCP client, which is what makes this discoverable at all."""
    return mcp_server.describe(_mcp_url(request))


@app.get("/mcp/tools")
def mcp_tools():
    return {"tools": mcp_server.tool_list(), "count": len(mcp_server.TOOLS),
            "instructions": mcp_server.INSTRUCTIONS}


@app.get("/mcp/config")
def mcp_config(request: Request):
    return mcp_server.client_config(_mcp_url(request))


@app.post("/mcp")
async def mcp_call(request: Request, authorization: str = Header(default=None)):
    """One JSON-RPC message, or a batch array of them."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=mcp_server._error(
            None, -32700, 'parse error: body is not JSON'))

    ctx = _mcp_ctx(authorization)
    if isinstance(body, list):
        out = [r for r in (mcp_server.handle(item, ctx) for item in body)
               if r is not None]
        # An all-notification batch is answered with 202 and no body, per spec.
        return JSONResponse(content=out) if out else Response(status_code=202)

    response = mcp_server.handle(body, ctx)
    if response is None:
        return Response(status_code=202)
    return JSONResponse(content=response)


@app.post("/mcp/tools/{name}")
def mcp_tool_call(name: str, body: dict = Body(default={}),
                  authorization: str = Header(default=None)):
    """One tool, called directly -- the same handler tools/call runs, for
    clients that would rather send a plain POST than a JSON-RPC envelope."""
    try:
        return {"result": mcp_server.call_tool(name, body or {},
                                               _mcp_ctx(authorization))}
    except mcp_server.Refused as e:
        raise HTTPException(status_code=401 if 'module token' in str(e) else 400,
                            detail=str(e))
    except TypeError as e:
        raise HTTPException(status_code=400, detail=f"{name}: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{name}: {e}")


@app.post("/{fn}")
def dispatch(fn: str, body: dict = Body(default={}),
             authorization: str = Header(default=None)):
    """Call a module function by name with a JSON body of its arguments."""
    if fn not in OPEN_FNS and fn not in GUARDED_FNS:
        raise HTTPException(status_code=404, detail=f"unknown function: {fn}")
    check_auth(fn, authorization)

    mod = get_mod()
    fn_obj = getattr(mod, fn, None)
    if fn_obj is None or not callable(fn_obj):
        raise HTTPException(status_code=404, detail=f"unknown function: {fn}")
    try:
        result = fn_obj(**(body or {}))
    except TypeError as e:
        raise HTTPException(status_code=400, detail=f"{fn}: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{fn}: {e}")

    if isinstance(result, dict) and 'error' in result:
        # Module errors are user-level (bad address, no funds), not server faults.
        raise HTTPException(status_code=400, detail=result['error'])
    return {"result": result}


if __name__ == "__main__":
    import uvicorn
    token = server_token()
    # ZCASH_REST_PORT before PORT: this process gets restarted by supervisors
    # whose own PORT is the web app's, and binding that would fight the app.
    port = int(os.environ.get("ZCASH_REST_PORT") or os.environ.get("PORT") or 8930)
    print(f"zcash api on :{port}  (guarded-fn token: {token[:8]}… in {secret_path()})")
    uvicorn.run(app, host=os.environ.get("ZCASH_API_HOST", "127.0.0.1"), port=port)
