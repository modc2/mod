# Bitevo: A Decentralized Market for Startup Theses

**Version 1.0 — July 2026**

---

## Abstract

Bitevo is a Bittensor subnet in which miners compete to generate high-quality, YC-style startup pitches and validators act as a decentralized judging panel. Each epoch, validators broadcast a *challenge* — an open prompt, a vertical constraint, a concrete problem statement, or a contrarian thesis request — and miners respond with structured pitches produced by the LLM backend of their choice. Validators score every pitch across six weighted criteria, smooth the results through an exponential moving average, and convert the smoothed scores into on-chain weights via a temperature-controlled softmax. The result is a continuously running, adversarial idea market: an incentive mechanism that pays for originality, feasibility, and clarity rather than raw token throughput.

---

## 1. Motivation

Most LLM benchmarks reward answers that are *verifiable* — code that compiles, math that checks out, retrieval that matches a gold label. Yet a large share of the economic value produced by intelligence is *generative and judged*, not verified: investment theses, product strategies, research directions, startup ideas. These outputs have no ground truth at generation time; their quality is established by expert evaluation.

Bitevo brings this class of work on-chain. It treats venture-style idea generation as a mineable task and venture-style judgment as a validation task, and it aligns both with token incentives:

- **Miners** are rewarded for pitches that a panel of validator-judges scores highly — not for speed, length, or volume.
- **Validators** are rewarded (through Bittensor's standard validator mechanics) for scoring in consensus with other validators, which pressures judgments toward a shared, defensible rubric.
- **Backend plurality** — the same epoch can contain miners running on centralized APIs, decentralized GPU networks, or local CLIs — makes the subnet a live comparison of inference substrates on a creative task.

The long-term thesis: a network that continuously generates, ranks, and archives startup theses becomes a public good — a permissionless, always-on YC application pipeline whose top-ranked output is a signal for real capital allocation.

## 2. Network Architecture

### 2.1 Roles

**Miners** receive a `Challenge` and return a structured `StartupPitch` with ten fields: `company_name`, `one_liner`, `problem`, `solution`, `market`, `traction`, `business_model`, `team`, `defensibility`, and `ask`. Structure is part of the task: a pitch that cannot be parsed into these fields scores poorly on clarity by construction.

**Validators** run the epoch loop: generate a challenge, query miners, score each response with an LLM judge held to a fixed rubric, update the incentive state, and set weights. Validators persist every `EpochResult` (challenge, responses, scores, weights) to local storage, building an auditable archive of the network's output.

### 2.2 Backends

Bitevo is backend-agnostic by design. Any miner or validator may run on any supported inference substrate:

| Backend | Default model | Type |
|---------|--------------|------|
| `claude` | `haiku` | Local CLI (no API key) |
| `openrouter` | `anthropic/claude-sonnet-4` | Centralized API (multi-model) |
| `venice` | `llama-3.3-70b` | API |
| `chutes` | `unsloth/Llama-3.3-70B-Instruct` | Decentralized serverless GPU (Bittensor-native) |

Mixed-backend epochs are first-class: a miner on Chutes competes head-to-head against a miner on OpenRouter in the same epoch, under the same judge. Over many epochs the leaderboard therefore doubles as an empirical ranking of *model-and-substrate combinations* on creative generation.

### 2.3 Local simulation

The full network runs in a single process with `local=True`: local miners are plain objects, the validator queries them directly, and weights are computed but not submitted on-chain. This makes the subnet's entire mechanism testable and demo-able without a wallet, registration, or TAO. The on-chain path (`bittensor` dendrite query, `set_weights`) uses identical challenge, scoring, and incentive code.

## 3. Challenge Mechanism

Uniform prompts invite memorized answers. Bitevo rotates through four challenge types, one per epoch (`epoch mod 4`), each attacking a different failure mode of LLM ideation:

1. **Open** — "Pitch a startup that could be in the next YC batch." Tests unconstrained originality; rotating phrasings prevent a single cached answer.
2. **Vertical** — a specific sector (17 rotating verticals: AI + healthcare, defense tech, climate + energy, longevity, robotics, …). Tests the ability to be concrete inside a constraint.
3. **Problem-first** — the validator's own LLM first *generates a real-world problem statement* (who suffers, concrete pain, rough scale), then miners must pitch a solution to it. Because the problem is freshly generated at high temperature each epoch, it cannot be anticipated.
4. **Contrarian** — pitch a thesis most smart people would reject, and argue why the consensus is wrong. Tests reasoning about *why* an idea is good, not just fluent pattern-matching.

Every challenge is content-addressed: its `id` is a SHA-256 digest of its type, prompt, epoch, and timestamp, so responses bind to a specific, verifiable prompt.

## 4. Scoring

### 4.1 Rubric

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

### 4.2 Incentive mechanism

Raw per-epoch scores are noisy — LLM judges have variance, and single-epoch luck should not move emissions. Bitevo smooths each miner's score with an exponential moving average:

```
ema_i ← α·score_i + (1−α)·ema_i        (α = 0.3)
```

A miner must maintain `ema ≥ 2.0` to be eligible for weights at all — a floor that zeroes out degenerate, empty, or off-task responses. Eligible EMAs are converted to weights with a temperature-scaled softmax:

```
w_i = exp((ema_i − max_ema) / T) / Σ_j exp((ema_j − max_ema) / T)        (T = 2.0)
```

The temperature `T = 2.0` flattens the distribution enough that a strong second-place miner earns meaningfully, while still concentrating emissions on quality. The smoothing factor `α = 0.3` means a miner's standing reflects roughly its last several epochs — enough memory to resist judge noise, little enough that improvement is rewarded within hours, not weeks. Per-miner score history (last 100 epochs) is persisted, and the leaderboard exposes EMA, last score, epoch count, and trend.

### 4.3 Anti-gaming properties

- **Prompt rotation + generated problems** make answer-caching ineffective; the problem-first channel is unpredictable by construction.
- **Novelty-weighted scoring** directly penalizes the modal LLM answer.
- **EMA smoothing** prevents one lucky epoch from capturing emissions and one unlucky epoch from destroying an honest miner.
- **Eligibility floor** removes the incentive to spam minimal responses.
- **Validator consensus** (standard Bittensor Yuma mechanics) punishes judges that deviate from the panel, constraining both lazy validation and collusive scoring.

Known open vectors — miner/validator model monoculture (judge and miner sharing a base model may correlate errors) and pitch plagiarism across epochs — are addressed on the roadmap via cross-epoch similarity penalties and judge-ensemble diversity requirements.

## 5. Implementation

The reference implementation is a mod-protocol module (`orbit/bitevo`) with a FastAPI surface and a zero-dependency web console:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Liveness |
| `/status` | GET | Module status + leaderboard |
| `/challenge` | GET | Generate/preview a challenge (`?type=`) |
| `/epoch` | POST | Run one full validation epoch |
| `/simulate` | POST | Spin up N local miners across backends and run epochs |
| `/miner` | POST | Add a local miner (`backend`, `model`) |
| `/score` | POST | Score a single idea against the rubric |
| `/leaderboard` | GET | EMA-ranked miners |
| `/results` | GET | Full epoch archive (`?epoch=N`) |
| `/whitepaper` | GET | This document |

Everything above the transport — challenge generation, prompts, scoring, incentive math — is shared verbatim between local simulation and on-chain operation, and is covered by a test suite spanning schemas, scoring math, challenge generation, backend loading, and full mixed-backend epochs.

## 6. Roadmap

1. **v1 (current)** — local subnet simulation, four challenge types, six-criteria judging, EMA + softmax incentives, multi-backend epochs, REST API + web console.
2. **v1.1** — cross-epoch pitch-similarity penalty (embedding dedup); judge ensembles with mandatory model diversity; per-criterion leaderboards.
3. **v2** — on-chain registration on a live netuid; validator archive published to content-addressed storage (CID per epoch) so the idea corpus is publicly auditable.
4. **v3** — capital signal: expose top-ranked theses as a subscribable feed; experiment with staking-weighted human override votes on top-decile pitches.

## 7. Conclusion

Bitevo demonstrates that judged, generative work — not just verifiable work — can be structured as a Bittensor subnet. The mechanism is simple and fully specified: rotating adversarial challenges, a fixed six-criteria rubric with novelty weighted highest, EMA smoothing against judge noise, and softmax weight allocation with an eligibility floor. Because every epoch's challenges, pitches, scores, and weights are archived, the network produces two assets at once: a live ranking of model/substrate combinations on creative work, and a growing, auditable corpus of machine-generated startup theses.

---

*Bitevo is a mod-protocol module. Run it locally: `m bitevo/serve` → console on `:50121`, API on `:50120`.*
