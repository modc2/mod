# Bitevo: An Evolutionary Market for Ideas and Predictions

**Version 2.0 — September 2026**

---

## Abstract

Bitevo is a Bittensor subnet that pays for two kinds of intelligence under one incentive mechanism. In **Track I — judged generation**, miners compete to produce high-quality, YC-style startup pitches and validators act as a decentralized judging panel: rotating adversarial challenges, a fixed six-criteria rubric with novelty weighted highest, EMA smoothing against judge noise, and softmax weight allocation. In **Track II — evolutionary forecasting**, the network breeds populations of predictor *genomes* — small nonlinear vector-autoregressive models in which every variable reads lagged values of every other — under selection, crossover, and mutation. Fitness is measured exclusively as walk-forward error on a held-out **future** window, so the evolutionary process fits multivariate predictions of the future rather than curve-fitting the past. Both tracks feed the same temperature-controlled softmax that converts quality into on-chain weights. The result is a continuously running market where judged creativity and verified foresight are priced by one mechanism, and where every epoch, pitch, score, genome, and forecast is archived and auditable. The reference implementation is local-first: the entire network — miners, validators, evolution, console — runs on a single machine with no external services required.

---

## 1. Motivation

Most LLM benchmarks reward answers that are *verifiable* — code that compiles, math that checks out, retrieval that matches a gold label. Yet a large share of the economic value produced by intelligence is *generative and judged*, not verified: investment theses, product strategies, research directions, startup ideas. And a further share is *predictive*: claims about the future whose quality can only be established by waiting for the future to arrive.

Bitevo brings both classes of work on-chain and aligns them with token incentives:

- **Miners (Track I)** are rewarded for pitches that a panel of validator-judges scores highly — not for speed, length, or volume.
- **Genomes (Track II)** are rewarded for out-of-sample forecasting skill on multivariate series — measured against realized future values, the one judge that cannot be argued with.
- **Validators** are rewarded (through Bittensor's standard validator mechanics) for scoring in consensus with other validators, which pressures judgments toward a shared, defensible rubric.
- **Backend plurality** — the same epoch can contain miners running on centralized APIs, decentralized GPU networks, or local CLIs — makes the subnet a live comparison of inference substrates on creative work.

The long-term thesis: a network that continuously generates and ranks startup theses, while simultaneously evolving and verifying forecasting models, becomes a public good — a permissionless pipeline whose top-ranked ideas *and* best-calibrated predictors are signals for real capital allocation.

## 2. Network Architecture

### 2.1 Roles

**Miners** receive a `Challenge` and return a structured `StartupPitch` with ten fields: `company_name`, `one_liner`, `problem`, `solution`, `market`, `traction`, `business_model`, `team`, `defensibility`, and `ask`. Structure is part of the task: a pitch that cannot be parsed into these fields scores poorly on clarity by construction.

**Validators** run the epoch loop: generate a challenge, query miners, score each response with an LLM judge held to a fixed rubric, update the incentive state, and set weights. Validators persist every `EpochResult` (challenge, responses, scores, weights) to local storage, building an auditable archive of the network's output.

**Genomes** are Track II's miners: compact multivariate predictors evolved rather than prompted. A genome's entire behavior is a few hundred numbers — inspectable, diffable, and reproducible from a seed.

### 2.2 Backends

Bitevo is backend-agnostic by design. Any miner or validator may run on any supported inference substrate:

| Backend | Default model | Type |
|---------|--------------|------|
| `claude` | `haiku` | Local CLI (no API key) |
| `openrouter` | `anthropic/claude-sonnet-4` | Centralized API (multi-model) |
| `venice` | `llama-3.3-70b` | API |
| `chutes` | `unsloth/Llama-3.3-70B-Instruct` | Decentralized serverless GPU (Bittensor-native) |

Mixed-backend epochs are first-class: a miner on Chutes competes head-to-head against a miner on OpenRouter in the same epoch, under the same judge. Over many epochs the leaderboard therefore doubles as an empirical ranking of *model-and-substrate combinations* on creative generation. Track II needs no LLM backend at all — evolution runs in pure Python on the standard library.

### 2.3 Local-first operation

The full network runs in a single process with `local=True`: local miners are plain objects, the validator queries them directly, evolution runs in-process, and weights are computed but not submitted on-chain. This makes the subnet's entire mechanism testable and demo-able without a wallet, registration, or TAO. The on-chain path (`bittensor` dendrite query, `set_weights`) uses identical challenge, scoring, incentive, and evolution code. All state — incentive EMAs, epoch archives, evolved populations — lives in plain JSON under `~/.bitevo`, owned by the operator, portable by copy.

## 3. Track I — Judged Generation

### 3.1 Challenge mechanism

Uniform prompts invite memorized answers. Bitevo rotates through four challenge types, one per epoch (`epoch mod 4`), each attacking a different failure mode of LLM ideation:

1. **Open** — "Pitch a startup that could be in the next YC batch." Tests unconstrained originality; rotating phrasings prevent a single cached answer.
2. **Vertical** — a specific sector (17 rotating verticals: AI + healthcare, defense tech, climate + energy, longevity, robotics, …). Tests the ability to be concrete inside a constraint.
3. **Problem-first** — the validator's own LLM first *generates a real-world problem statement* (who suffers, concrete pain, rough scale), then miners must pitch a solution to it. Because the problem is freshly generated at high temperature each epoch, it cannot be anticipated.
4. **Contrarian** — pitch a thesis most smart people would reject, and argue why the consensus is wrong. Tests reasoning about *why* an idea is good, not just fluent pattern-matching.

Every challenge is content-addressed: its `id` is a SHA-256 digest of its type, prompt, epoch, and timestamp, so responses bind to a specific, verifiable prompt.

### 3.2 Scoring rubric

Each pitch is scored 0–10 on six criteria by an LLM judge held to a fixed system prompt. The composite is a weighted sum:

| Criterion | Weight | What it measures |
|-----------|--------|-----------------|
| Novelty | 0.25 | Is the idea non-obvious? Does it avoid the well-trodden? |
| Feasibility | 0.20 | Could a competent team ship this? |
| Market size | 0.20 | Is the market large or credibly becoming large? |
| Defensibility | 0.15 | Moat: network effects, data, switching costs |
| Clarity | 0.10 | Is the pitch crisp, specific, and well-structured? |
| Traction signal | 0.10 | Does it show evidence-mindedness — a path to proof? |

```
composite = 0.25·novelty + 0.20·feasibility + 0.20·market_size
          + 0.15·defensibility + 0.10·clarity + 0.10·traction_signal
```

Novelty carries the largest weight deliberately: the marginal value of the network is ideas that *aren't* the first thing every model says. Judge output is strict JSON; a response that fails to parse receives neutral default scores (3.0 across the board) and explicit feedback, so malformed judging degrades gracefully rather than corrupting weights.

### 3.3 Incentive mechanism

Raw per-epoch scores are noisy — LLM judges have variance, and single-epoch luck should not move emissions. Bitevo smooths each miner's score with an exponential moving average:

```
ema_i ← α·score_i + (1−α)·ema_i        (α = 0.3)
```

A miner must maintain `ema ≥ 2.0` to be eligible for weights at all — a floor that zeroes out degenerate, empty, or off-task responses. Eligible EMAs are converted to weights with a temperature-scaled softmax:

```
w_i = exp((ema_i − max_ema) / T) / Σ_j exp((ema_j − max_ema) / T)        (T = 2.0)
```

The temperature `T = 2.0` flattens the distribution enough that a strong second-place miner earns meaningfully, while still concentrating emissions on quality. The smoothing factor `α = 0.3` means a miner's standing reflects roughly its last several epochs — enough memory to resist judge noise, little enough that improvement is rewarded within hours, not weeks. Per-miner score history (last 100 epochs) is persisted, and the leaderboard exposes EMA, last score, epoch count, and trend.

## 4. Track II — Evolutionary Multivariate Forecasting

Track I's judge is a model; Track II's judge is reality. The network maintains a population of predictor genomes and evolves them under one selection pressure: **accuracy of multivariate predictions on data the genome has never seen — the future.**

### 4.1 The genome

A genome is a compact nonlinear vector-autoregressive (VAR) predictor over `V` standardized series. For each output variable `i`, the one-step-ahead prediction is:

```
ẑ_i(t+1) = b_i + Σ_{j≤V, l≤L} W_lin[i][j,l] · z_j(t−l)
              + a_i · tanh( Σ_{j≤V, l≤L} W_nl[i][j,l] · z_j(t−l) )
```

Three properties matter:

- **Fully multivariate.** Every output reads lagged values of *every* variable — cross-couplings (a driver series leading a follower, volatility excited by shocks) are representable and, on coupled data, necessary to win.
- **Structure is evolvable.** The lag depth `L` is itself a gene, mutated ±1 within bounds; coefficients are remapped when structure changes. Evolution searches model *shape*, not just model *weights*.
- **Bounded nonlinearity.** One `tanh` unit per output adds regime-sensitivity without the blowup risk of unconstrained recurrence; recursive rollouts are clamped in z-space.

A genome's identity is a content hash of its parameters — like challenges, genomes are content-addressed.

### 4.2 Fitness: the future, walk-forward

Each dataset is split at 80%: statistics for standardization come from the training region only, and **fitness is evaluated only past the split**. From a set of anchor points in the held-out region, the genome recursively rolls out an `h`-step forecast (each prediction is fed back as input — errors compound exactly as they would in production), and fitness is the negative root-mean-squared error across all variables, all steps, and all anchors, minus a parsimony term:

```
fitness = −RMSE_future − λ·L        (λ = 0.004)
```

This is the mechanism's central commitment: a genome is never rewarded for reproducing data it was shaped on. Selection pressure points at generalization, multi-step, across every variable at once. The parsimony term breaks ties toward simpler structures — a deep-lag genome must *earn* its complexity.

### 4.3 The evolutionary loop

Each generation:

1. **Evaluate** every unevaluated genome on the future window.
2. **Elitism** — the top 2 genomes survive unchanged (their `age` counts survived generations, so long-lived genomes are visible on the leaderboard).
3. **Selection** — tournament of 3: sample three genomes, the fittest parents.
4. **Crossover** — row-level uniform: the child inherits each output variable's *entire* predictor row (linear weights, nonlinear weights, bias, amplitude) from one parent or the other. Rows are the natural modules of a VAR — recombination exchanges whole per-variable strategies rather than shredding them.
5. **Mutation** — Gaussian perturbation of weights (p = 0.15, σ = 0.12), occasional hard zeroing (p = 0.02, a sparsity pressure), and structural lag mutation (p = 0.10).

A quarter of the founding population are **persistence priors** — genomes hard-wired to predict "tomorrow equals today." Persistence is the classic baseline every forecaster must beat; seeding it means generation 1 already contains the null hypothesis, and anything that displaces it has demonstrated real skill.

### 4.4 One incentive mechanism, two tracks

Genome fitness flows into the *same* softmax that allocates Track I weights (fitness is pre-scaled so the shared `T = 2.0` softmax acts at an effective temperature of 0.1 in RMSE units — appropriately sharper, since Track II scores carry no judge noise). Elitism plays the role EMA smoothing plays in Track I: quality must persist across generations to keep earning. The two tracks are therefore the same economic statement — *smoothed, temperature-scaled, quality-proportional emission* — instantiated once for judged work and once for verified work.

### 4.5 Data: local-first by construction

Track II ships with three built-in synthetic generators, each deliberately cross-coupled so multivariate genomes dominate univariate ones:

| Dataset | Structure |
|---------|-----------|
| `coupled` | trend + cycle driver; follower responding at lag 6; stress excited by driver shocks |
| `harmonics` | three phase-shifted harmonic mixes — strongly predictable, ideal for demos |
| `regimes` | drift that flips sign at random; lagged follower; mean-reverting spread |

Any local CSV drops in the same slot (`csv=<path>`): header row of names, numeric columns become variables, non-numeric columns are ignored. Your data is read from disk and never leaves the machine — no upload, no third-party feed, no API key. The engine itself is standard-library Python: no numpy, no framework, no vendor.

### 4.6 Anti-gaming properties

- **The future cannot be bribed.** Track II's score is computed against realized values; there is no judge to persuade, collude with, or prompt-inject.
- **Walk-forward evaluation with recursive rollout** punishes models that only look one step ahead or that memorize the training region.
- **Train-region standardization** prevents information leaking from the evaluation window into preprocessing.
- **Parsimony pressure + sparsity mutation** resist bloat, the classic failure mode of evolutionary model search.
- **Persistence seeding** anchors the fitness scale: a population that cannot beat "no change" earns accordingly.
- In Track I: **prompt rotation + generated problems** defeat answer-caching; **novelty-weighted scoring** penalizes the modal LLM answer; **EMA smoothing** and the **eligibility floor** remove one-epoch luck and spam; **validator consensus** (standard Yuma mechanics) constrains lazy and collusive judging.

Known open vectors — judge/miner model monoculture in Track I, and overfitting to a *fixed* held-out window in Track II if the same window is reused indefinitely — are addressed on the roadmap via judge-ensemble diversity and rolling re-splits as new data arrives.

## 5. Implementation

The reference implementation is a mod-protocol module (`orbit/bitevo`) with a FastAPI surface and a zero-dependency web console (CONSOLE · EVOLVE · EPOCHS · WHITEPAPER):

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Liveness |
| `/status` | GET | Module status + leaderboard + evolution summary |
| `/challenge` | GET | Generate/preview a challenge (`?type=`) |
| `/epoch` | POST | Run one full validation epoch (Track I) |
| `/simulate` | POST | Spin up N local miners across backends and run epochs |
| `/miner` | POST | Add a local miner (`backend`, `model`) |
| `/score` | POST | Score a single idea against the rubric |
| `/leaderboard` | GET | EMA-ranked miners |
| `/results` | GET | Full epoch archive (`?epoch=N`) |
| `/evolve` | POST | Run evolution: generations, population, dataset/CSV, horizon |
| `/forecast` | GET | Best genome's multivariate forecast (`?horizon=`) |
| `/population` | GET | Genome leaderboard + fitness history |
| `/datasets` | GET | Built-in multivariate datasets |
| `/whitepaper` | GET | This document |

Evolution state persists to `~/.bitevo/evolution_state.json` (atomic writes) and resumes across restarts: calling `/evolve` again continues breeding the saved population unless `fresh=true` or the dataset changes. Everything above the transport — challenges, prompts, scoring, incentive math, the evolutionary engine — is shared verbatim between local simulation and on-chain operation, and covered by a test suite spanning schemas, scoring math, challenge generation, backend loading, evolution convergence, forecast shape, persistence round-trips, and full mixed-backend epochs.

## 6. Roadmap

1. **v1** — local subnet simulation, four challenge types, six-criteria judging, EMA + softmax incentives, multi-backend epochs, REST API + web console.
2. **v2 (current)** — Track II: evolutionary multivariate forecasting; genome populations with structural evolution, walk-forward future-window fitness, persistence baselines, CSV ingestion, EVOLVE console with fitness and forecast charts.
3. **v2.1** — rolling re-splits (the future window advances as data arrives, so genomes are perpetually re-verified); cross-epoch pitch-similarity penalty (embedding dedup); judge ensembles with mandatory model diversity; island-model populations with migration.
4. **v3** — on-chain registration on a live netuid; validator archive and genome lineages published to content-addressed storage (CID per epoch/generation) so both corpora are publicly auditable.
5. **v4** — capital signal: top-ranked theses as a subscribable feed; evolved forecasters applied to live on-chain series (subnet emissions, liquidity, prices) with staking-weighted human override on top-decile outputs.

## 7. Conclusion

Bitevo demonstrates that a Bittensor subnet can price two very different kinds of cognitive work with one mechanism. Judged generation shows that creative output with no ground truth can still be ranked defensibly — rotating adversarial challenges, a fixed rubric, smoothing against judge variance. Evolutionary forecasting shows the complement: where ground truth *does* exist, it should be the only judge — populations of multivariate predictor genomes bred exclusively on their ability to fit predictions of the future, with the past used only to form them and never to score them. Both resolve to the same softmax, the same archive, the same local-first JSON on the operator's own disk. The network's two assets compound: a growing, auditable corpus of machine-generated startup theses, and a lineage of verified forecasters whose skill was proven the only way skill can be — ahead of time.

---

*Bitevo is a mod-protocol module. Run it locally: `m bitevo/serve` → console on `:50121`, API on `:50120`. Evolve: `m bitevo/evolve` · Forecast: `m bitevo/forecast`.*
