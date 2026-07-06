import os
import json
import requests as req
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
import mod as m

app = FastAPI(title="Chutes API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Helpers ──────────────────────────────────────────────────────────

def get_chutes(api_key=None):
    kwargs = {}
    if api_key:
        kwargs['api_key'] = api_key
    return m.mod('chutes')(**kwargs)


def get_api_key(request: Request):
    return request.headers.get("x-api-key", os.environ.get("CHUTES_API_KEY", ""))


def error_response(e):
    if hasattr(e, 'response') and e.response is not None:
        return JSONResponse(
            status_code=e.response.status_code,
            content={"error": e.response.text},
        )
    return JSONResponse(status_code=500, content={"error": str(e)})


# ── Chat ─────────────────────────────────────────────────────────────

@app.post("/chat")
async def chat(request: Request):
    body = await request.json()
    api_key = get_api_key(request)
    stream = body.get("stream", False)

    if stream:
        return _stream_chat(body, api_key)

    try:
        chutes = get_chutes(api_key)
        messages = body.get("messages", [])
        model = body.get("model")
        temperature = body.get("temperature", 0.7)
        max_tokens = body.get("max_tokens", 4096)

        result = chutes.chat(
            messages, model=model, stream=False,
            temperature=temperature, max_tokens=max_tokens,
        )
        return result
    except Exception as e:
        return error_response(e)


def _stream_chat(body, api_key):
    base_url = os.environ.get("CHUTES_BASE_URL", "https://api.chutes.ai")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    def generate():
        resp = req.post(
            f"{base_url}/v1/chat/completions",
            json=body, headers=headers, stream=True, timeout=120,
        )
        resp.raise_for_status()
        for line in resp.iter_lines(decode_unicode=True):
            if line:
                yield line + "\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    })


# ── Images ───────────────────────────────────────────────────────────

@app.post("/images")
async def images(request: Request):
    body = await request.json()
    api_key = get_api_key(request)

    try:
        chutes = get_chutes(api_key)
        prompt = body.get("prompt", "")
        model = body.get("model")
        size = body.get("size", "1024x1024")
        n = body.get("n", 1)
        response_format = body.get("response_format", "url")

        result = chutes.generate_image(
            prompt, model=model, size=size, n=n, response_format=response_format,
        )
        return result
    except Exception as e:
        return error_response(e)


# ── Models ───────────────────────────────────────────────────────────

@app.get("/models")
async def models(request: Request):
    api_key = get_api_key(request)
    search = request.query_params.get("q", "")

    try:
        chutes = get_chutes(api_key)
        result = chutes.models(search=search or None)
        return result
    except Exception as e:
        return error_response(e)


# ── Forward (generic) ────────────────────────────────────────────────

@app.post("/forward")
async def forward(request: Request):
    body = await request.json()
    api_key = get_api_key(request)
    action = body.pop("action", body.pop("fn", None))

    try:
        chutes = get_chutes(api_key)
        if action and hasattr(chutes, action) and not action.startswith("_"):
            fn = getattr(chutes, action)
            result = fn(**body) if callable(fn) else fn
            return {"result": result}

        result = chutes.forward(**body)
        return {"result": result}
    except Exception as e:
        return error_response(e)


# ── Health ───────────────────────────────────────────────────────────

@app.get("/")
async def health():
    return {"name": "chutes", "status": "ok"}
