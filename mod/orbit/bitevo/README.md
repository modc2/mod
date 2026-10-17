# Bitevo

Bittensor subnet with two tracks under one incentive mechanism:

- **Track I — judged generation:** miners generate YC-style startup pitches, validators LLM-judge them on a six-criteria rubric.
- **Track II — evolutionary forecasting:** populations of multivariate predictor genomes (nonlinear VAR models, every variable reads lagged values of every other) evolve by selection/crossover/mutation, with fitness measured as walk-forward error on a held-out **future** window — the population is bred to fit multivariate predictions of the future, never to fit the past. Pure stdlib, local-first; evolve against built-in coupled datasets or any local CSV.

**Whitepaper:** [WHITEPAPER.md](WHITEPAPER.md) — mechanism design, scoring rubric, EMA/softmax incentives, genome design, walk-forward fitness. Also served at `/whitepaper` on the API and as the WHITEPAPER tab in the web console.

**Web console:** `m bitevo/serve` → `http://localhost:50121` — CONSOLE (leaderboard, epochs, judge your own idea), EVOLVE (run evolution, fitness curve, multivariate forecast chart, genome leaderboard), EPOCHS (full archive), WHITEPAPER. Zero-dep static app (`app/server.py` + `app/index.html`) proxying `/api/*` to the FastAPI backend.

## Backends

| Backend | Model | Type |
|---------|-------|------|
| `openrouter` | `anthropic/claude-sonnet-4` | API (multi-model) |
| `venice` | `llama-3.3-70b` | API |
| `chutes` | `unsloth/Llama-3.3-70B-Instruct` | Decentralized GPU (Bittensor-native) |

Chutes.ai runs on decentralized serverless GPUs — the natural inference layer for Bittensor subnets. Miners and validators can use any backend; mixed-backend epochs are supported.

## Structure

```
bitevo/
├── bitevo/
│   └── mod.py          # Anchor — Mod class, CLI entry
├── neurons/
│   ├── miner.py        # Miner: generates startup pitches via LLM
│   └── validator.py    # Validator: scores pitches, sets weights
├── core/
│   ├── schemas.py      # Pydantic models (Challenge, Pitch, Score, etc.)
│   ├── prompts.py      # System/user prompts for miner & validator
│   ├── scoring.py      # Composite scoring + EMA incentive mechanism
│   ├── evolve.py       # Evolutionary engine: genomes, datasets, walk-forward fitness
│   └── challenge.py    # Challenge generator (open, vertical, contrarian, etc.)
├── api/
│   └── api.py          # FastAPI REST API
├── tests/
│   └── test_bitevo.py  # Network test suite
├── config.json
└── README.md
```

## Usage

### Python

```python
import mod as m
b = m.mod('bitevo')()

# Simulate with all backends (openrouter, venice, chutes)
b.simulate(n_miners=3)

# Simulate with chutes only
b.simulate(n_miners=3, backends=['chutes'])

# Single epoch
b.epoch()

# Leaderboard
b.leaderboard()

# Add a miner on chutes
b.add_miner(backend='chutes')

# Score an idea
b.score_idea("AI-powered invoice reconciliation for SMBs")

# List available backends
b.backends()

# Track II: evolve multivariate forecasters (resumes saved population)
b.evolve(generations=25, population=40, dataset='coupled', horizon=10)
b.evolve(csv='~/data/my_series.csv')   # your own local data
b.forecast(horizon=12)                 # best genome predicts the future
b.population()                         # genome leaderboard + fitness history
b.datasets()                           # built-in multivariate datasets
```

### CLI

```bash
m bitevo/simulate n_miners=3
m bitevo/simulate n_miners=3 backends='["chutes"]'
m bitevo/simulate n_miners=6 backends='["chutes","openrouter","venice"]'
m bitevo/epoch
m bitevo/leaderboard
m bitevo/add_miner backend=chutes
m bitevo/score_idea idea="Decentralized GPU compute marketplace"
m bitevo/evolve generations=25 dataset=coupled
m bitevo/evolve csv=~/data/my_series.csv fresh=true
m bitevo/forecast horizon=12
m bitevo/population
m bitevo/datasets
m bitevo/backends
m bitevo/status
m bitevo/serve
m bitevo/kill
```

## Testing

Run the test suite to verify backends, schemas, scoring, and full network epochs.

```bash
# Quick tests (schemas, scoring, challenge gen, backend load — no LLM calls)
m bitevo/test

# Test specific backends
m bitevo/test backends='["chutes"]'

# Full test suite (includes live LLM calls, miner/validator, epochs)
m bitevo/test full=true

# Full test on chutes only
m bitevo/test backends='["chutes"]' full=true
```

### Test coverage

| Test | What it checks | LLM call? |
|------|---------------|-----------|
| `test_schemas` | All Pydantic models instantiate and serialize | No |
| `test_scoring` | Composite score, normalization, incentive mechanism | No |
| `test_challenge_gen` | Challenge generator produces valid challenges | No |
| `test_backend_load` | Backend module loads and has `forward()` | No |
| `test_backend_forward` | Live LLM completion through backend | Yes |
| `test_miner_forward` | Miner generates a startup pitch | Yes |
| `test_validator_score` | Validator scores a mock pitch | Yes |
| `test_epoch_local` | Full epoch: challenge → mine → score → weights | Yes |
| `test_multi_backend_epoch` | Mixed-backend epoch (all 3 backends) | Yes |

## API

Start with `m bitevo/serve`, docs at `http://localhost:50120/docs`.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/status` | GET | Module status and leaderboard |
| `/simulate` | POST | Run local simulation with N miners |
| `/epoch` | POST | Run a single validation epoch |
| `/leaderboard` | GET | Miner leaderboard sorted by EMA score |
| `/results` | GET | Get epoch results (`?epoch=N`) |
| `/miner` | POST | Add a local miner |
| `/challenge` | GET | Generate/preview a challenge |
| `/score` | POST | Score a single startup idea |
| `/evolve` | POST | Evolve multivariate forecaster genomes (Track II) |
| `/forecast` | GET | Best genome's multivariate forecast (`?horizon=N`) |
| `/population` | GET | Genome leaderboard + fitness history |
| `/datasets` | GET | Built-in multivariate datasets |
| `/backends` | GET | Supported LLM backends |
| `/whitepaper` | GET | Whitepaper (markdown) |

## Architecture

**Miners** receive a challenge prompt and generate a structured startup pitch (company name, problem, solution, market, traction, etc.) using their configured LLM backend.

**Validators** score each pitch on 6 criteria (novelty, feasibility, market_size, clarity, defensibility, traction_signal), compute a weighted composite score, and update an EMA-based incentive mechanism that determines miner weights.

**Backends** are interchangeable — a miner on `chutes` competes against miners on `openrouter` or `venice` in the same epoch. The decentralized chutes backend is the natural fit for Bittensor subnet operators running on distributed GPU infrastructure.

**Genomes** (Track II) are content-addressed multivariate predictors evolved locally: lag structure and coefficients are the genes, tournament selection + row-level crossover + Gaussian/structural mutation are the operators, and negative future-window RMSE (walk-forward, recursive rollout, parsimony-penalized) is the fitness. Emission weights over the population reuse the same softmax as Track I. State persists to `~/.bitevo/evolution_state.json` and resumes across restarts.
