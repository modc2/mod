"""
polycopy api

FastAPI gateway over polycopy_rs (Rust/PyO3 engine) + Python scraper
for historical trade discovery.

Engine routes hit polycopy_rs directly. The scraper scans 7 days of
V2/V3 swap events across all chains and filters for active traders
(>= N trades/day).

Endpoints:
    GET  /health              - health check
    GET  /status              - engine status
    GET  /wallets             - watched wallets
    GET  /scores              - trader scores
    GET  /trades              - recent trades
    GET  /rpc                 - rpc pool stats
    GET  /config              - current config
    POST /forward             - mod protocol dispatch
    POST /start               - start engine
    POST /stop                - stop engine
    POST /watch               - add wallet
    POST /unwatch             - remove wallet
    POST /pause               - pause chain
    POST /unpause             - unpause chain
    POST /execute             - manual trade
    POST /proxy               - set proxy address
    POST /config              - set config value
    POST /scrape              - start historical scrape
    GET  /scrape/status       - scrape progress
    GET  /scrape/traders      - filtered active traders

Usage:
    uvicorn api.api:app --host 0.0.0.0 --port 50130 --reload
"""
import asyncio
import json
import os
import sys
from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List

# ── paths ───────────────────────────────────────────────────────────

mod_dir = os.path.join(os.path.dirname(__file__), '..')
module_root = os.path.join(mod_dir, '..')
sys.path.insert(0, mod_dir)
sys.path.insert(0, module_root)

# ── config ──────────────────────────────────────────────────────────

config_path = os.path.join(module_root, 'config.json')
if os.path.exists(config_path):
    with open(config_path) as f:
        config = json.load(f)
else:
    config = {}

# ── engine (rust) ───────────────────────────────────────────────────

try:
    import polycopy_rs
    engine = polycopy_rs.PolycopyEngine(json.dumps(config))
    HAS_ENGINE = True
except ImportError:
    engine = None
    HAS_ENGINE = False

# ── scraper (python) ────────────────────────────────────────────────

from scraper import Scraper
scraper: Optional[Scraper] = None

# ── fallback mod ────────────────────────────────────────────────────

from mod import Mod
mod = Mod(config_path=config_path)

# ── app ─────────────────────────────────────────────────────────────

app = FastAPI(title='polycopy', version='0.1.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# ── models ──────────────────────────────────────────────────────────

class ForwardRequest(BaseModel):
    cmd: str = 'status'
    kwargs: dict = {}

class WatchRequest(BaseModel):
    address: str
    label: Optional[str] = None

class ConfigRequest(BaseModel):
    key: str
    value: str

class ChainRequest(BaseModel):
    chain_id: int

class ProxyRequest(BaseModel):
    chain_id: int
    address: str

class TradeRequest(BaseModel):
    trade_json: str

class ScrapeRequest(BaseModel):
    min_trades_per_day: int = 10
    lookback_days: int = 7
    chains: List[int] = [8453, 137, 42161]

# ── helpers ─────────────────────────────────────────────────────────

def _need_engine():
    if not HAS_ENGINE:
        return {'error': 'polycopy_rs not built — run: cd polycopy/polycopy-rs && maturin develop --release'}
    return None

# ── engine routes ───────────────────────────────────────────────────

@app.get('/health')
def health():
    return {'status': 'ok', 'engine': HAS_ENGINE}

@app.get('/status')
def status():
    if HAS_ENGINE:
        return {
            'running': engine.is_running(),
            'engine': 'polycopy_rs',
            'config': {k: v for k, v in config.items() if k != 'private_key'},
        }
    return mod.status()

@app.get('/wallets')
def wallets():
    err = _need_engine()
    if err: return err
    return json.loads(engine.get_wallets())

@app.get('/scores')
def scores():
    err = _need_engine()
    if err: return err
    return json.loads(engine.get_scores())

@app.get('/trades')
def trades(limit: int = 20):
    err = _need_engine()
    if err: return err
    return json.loads(engine.get_trades(limit))

@app.get('/rpc')
def rpc():
    err = _need_engine()
    if err: return err
    return json.loads(engine.get_rpc_stats())

@app.get('/config')
def get_config():
    return {k: v for k, v in config.items() if k != 'private_key'}

@app.post('/forward')
def forward(req: ForwardRequest):
    return mod.forward(cmd=req.cmd, **req.kwargs)

@app.post('/start')
def start():
    err = _need_engine()
    if err: return err
    engine.start()
    return {'status': 'running'}

@app.post('/stop')
def stop():
    err = _need_engine()
    if err: return err
    engine.stop()
    return {'status': 'stopped'}

@app.post('/watch')
def watch(req: WatchRequest):
    err = _need_engine()
    if err: return err
    engine.add_wallet(req.address, req.label)
    return {'watched': req.address}

@app.post('/unwatch')
def unwatch(req: WatchRequest):
    err = _need_engine()
    if err: return err
    engine.remove_wallet(req.address)
    return {'unwatched': req.address}

@app.post('/pause')
def pause(req: ChainRequest):
    err = _need_engine()
    if err: return err
    tx = engine.pause(req.chain_id)
    return {'paused': req.chain_id, 'tx': tx}

@app.post('/unpause')
def unpause(req: ChainRequest):
    err = _need_engine()
    if err: return err
    tx = engine.unpause(req.chain_id)
    return {'unpaused': req.chain_id, 'tx': tx}

@app.post('/execute')
def execute(req: TradeRequest):
    err = _need_engine()
    if err: return err
    tx = engine.execute_trade(req.trade_json)
    return {'tx': tx}

@app.post('/proxy')
def set_proxy(req: ProxyRequest):
    err = _need_engine()
    if err: return err
    engine.set_proxy_address(req.chain_id, req.address)
    return {'chain_id': req.chain_id, 'proxy': req.address}

@app.post('/config')
def set_config(req: ConfigRequest):
    return mod.set_config(key=req.key, value=req.value)

# ── scraper routes ──────────────────────────────────────────────────

def _run_scrape(s: Scraper):
    """Run scraper in a new event loop (for background task)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(s.run())
    finally:
        loop.close()

@app.post('/scrape')
def scrape(req: ScrapeRequest, background: BackgroundTasks):
    """Start a historical scrape. Runs in background — poll /scrape/status."""
    global scraper
    if scraper and scraper._running:
        return {'error': 'scrape already running', 'progress': scraper.get_progress()}

    scraper = Scraper(
        min_trades_per_day=req.min_trades_per_day,
        lookback_days=req.lookback_days,
        chains=req.chains,
    )
    background.add_task(_run_scrape, scraper)
    return {
        'started': True,
        'min_trades_per_day': req.min_trades_per_day,
        'lookback_days': req.lookback_days,
        'chains': req.chains,
    }

@app.get('/scrape/status')
def scrape_status():
    if not scraper:
        return {'error': 'no scrape started'}
    return scraper.get_progress()

@app.get('/scrape/traders')
def scrape_traders(min_trades_per_day: int = 10):
    """Get active traders from the last scrape, filtered by min trades/day."""
    if not scraper:
        return {'error': 'no scrape started'}
    if scraper._running:
        return {'error': 'scrape still running', 'progress': scraper.get_progress()}
    traders = scraper.get_traders(min_trades_per_day=min_trades_per_day)
    return {
        'min_trades_per_day': min_trades_per_day,
        'total_found': len(traders),
        'traders': traders,
    }
