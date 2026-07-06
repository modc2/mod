import json
import time
import os
import mod as m
from typing import List, Dict, Optional
from core.schemas import (
    Challenge, MinerResponse, ValidatorScore, ScoreBreakdown, EpochResult
)
from core.prompts import VALIDATOR_SYSTEM_PROMPT, VALIDATOR_USER_PROMPT
from core.scoring import composite_score, IncentiveMechanism
from core.challenge import ChallengeGenerator

DEFAULT_MODELS = {
    'openrouter': 'anthropic/claude-sonnet-4',
    'venice': 'llama-3.3-70b',
    'chutes': 'unsloth/Llama-3.3-70B-Instruct',
}


class BitevoValidator:

    def __init__(
        self,
        backend: str = 'openrouter',
        model: str = None,
        wallet_name: str = 'validator',
        hotkey: str = 'default',
        netuid: int = None,
        network: str = 'finney',
        local: bool = True,
        tempo: int = 120,
        timeout: int = 90,
        temperature: float = 0.3,
        storage_path: str = '~/.bitevo',
        **kwargs,
    ):
        self.backend = backend
        self.model = model or DEFAULT_MODELS.get(backend, 'anthropic/claude-sonnet-4')
        self.local = local
        self.wallet_name = wallet_name
        self.hotkey = hotkey
        self.netuid = netuid
        self.network = network
        self.tempo = tempo
        self.timeout = timeout
        self.temperature = temperature
        self.storage_path = os.path.expanduser(storage_path)

        self.epochs = 0
        self.epoch_time = 0

        self._llm = None
        self._wallet = None
        self._subtensor = None
        self._dendrite = None
        self._metagraph = None

        self.challenge_gen = ChallengeGenerator(llm_backend=backend)
        self._incentive_path = os.path.join(self.storage_path, 'incentive_state.json')
        self.incentive = IncentiveMechanism.load(self._incentive_path)
        self._local_miners = []

    @property
    def llm(self):
        if self._llm is None:
            self._llm = m.mod(self.backend)()
        return self._llm

    # ── Epoch ─────────────────────────────────────────────────────

    def epoch(self, challenge: Challenge = None, **kwargs) -> EpochResult:
        t0 = time.time()
        self.epochs += 1

        if challenge is None:
            challenge = self.challenge_gen.generate(epoch=self.epochs)
        print(f"[epoch {self.epochs}] challenge: {challenge.prompt[:80]}...")

        responses = self._query_miners(challenge)
        print(f"[epoch {self.epochs}] {len(responses)} responses received")

        scores = []
        for resp in responses:
            try:
                score = self._score_response(challenge, resp)
                scores.append(score)
                print(f"  miner {resp.miner_uid}: {score.composite_score:.2f} — {score.feedback[:60]}")
            except Exception as e:
                print(f"  miner {resp.miner_uid} scoring failed: {e}")

        epoch_scores = {s.miner_uid: s.composite_score for s in scores}
        weights = self.incentive.compute_weights(epoch_scores)

        if not self.local and self._subtensor and self.netuid:
            self._set_chain_weights(weights)

        result = EpochResult(
            epoch=self.epochs,
            challenge=challenge,
            responses=responses,
            scores=scores,
            weights=weights,
        )
        self._save_epoch(result)
        self.incentive.save(self._incentive_path)
        self.epoch_time = time.time()

        print(f"[epoch {self.epochs}] done in {time.time() - t0:.1f}s | weights: {weights}")
        return result

    # ── Query miners ──────────────────────────────────────────────

    def _query_miners(self, challenge: Challenge) -> List[MinerResponse]:
        if self.local:
            return self._query_local(challenge)
        return self._query_chain(challenge)

    def _query_local(self, challenge: Challenge) -> List[MinerResponse]:
        responses = []
        for miner in self._local_miners:
            try:
                resp = miner.forward(challenge)
                responses.append(resp)
            except Exception as e:
                print(f"  miner {miner.uid} failed: {e}")
        return responses

    def _query_chain(self, challenge: Challenge) -> List[MinerResponse]:
        challenge_data = challenge.model_dump_json()
        responses_raw = self._dendrite.query(
            self._metagraph.axons,
            challenge_data,
            timeout=self.timeout,
        )
        responses = []
        for raw in responses_raw:
            try:
                if raw and isinstance(raw, str):
                    data = json.loads(raw)
                    responses.append(MinerResponse(**data))
            except Exception:
                continue
        return responses

    # ── Scoring ───────────────────────────────────────────────────

    def _score_response(self, challenge: Challenge, response: MinerResponse) -> ValidatorScore:
        pitch = response.pitch
        user_prompt = VALIDATOR_USER_PROMPT.format(
            challenge_prompt=challenge.prompt,
            company_name=pitch.company_name,
            one_liner=pitch.one_liner,
            problem=pitch.problem,
            solution=pitch.solution,
            market=pitch.market,
            traction=pitch.traction,
            business_model=pitch.business_model,
            team=pitch.team,
            defensibility=pitch.defensibility,
            ask=pitch.ask,
        )

        raw = self.llm.forward(
            user_prompt,
            system_prompt=VALIDATOR_SYSTEM_PROMPT,
            model=self.model,
            temperature=self.temperature,
            stream=False,
        )

        scores_data = self._parse_scores(raw)
        feedback = scores_data.pop("feedback", "")
        breakdown = ScoreBreakdown(**{
            k: float(v) for k, v in scores_data.items()
            if k in ScoreBreakdown.model_fields
        })
        comp = composite_score(breakdown)

        return ValidatorScore(
            miner_uid=response.miner_uid,
            miner_hotkey=response.miner_hotkey,
            challenge_id=challenge.id,
            breakdown=breakdown,
            composite_score=comp,
            feedback=feedback,
        )

    def _parse_scores(self, raw: str) -> Dict:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if "```" in text:
                text = text[:text.rfind("```")]
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            return {
                "novelty": 3.0, "feasibility": 3.0, "market_size": 3.0,
                "clarity": 3.0, "defensibility": 3.0, "traction_signal": 3.0,
                "feedback": "Score parse failed — default scores applied.",
            }

    # ── Chain weights ─────────────────────────────────────────────

    def _set_chain_weights(self, weights: Dict[str, float]):
        import bittensor as bt
        import torch
        uids = [int(k) for k in weights.keys()]
        vals = list(weights.values())
        self._subtensor.set_weights(
            wallet=self._wallet,
            netuid=self.netuid,
            uids=torch.tensor(uids, dtype=torch.long),
            weights=torch.tensor(vals, dtype=torch.float32),
            wait_for_inclusion=True,
        )
        print(f"  weights set on chain for {len(uids)} miners")

    # ── Persistence ───────────────────────────────────────────────

    def _save_epoch(self, result: EpochResult):
        epoch_dir = os.path.join(self.storage_path, 'epochs')
        os.makedirs(epoch_dir, exist_ok=True)
        path = os.path.join(epoch_dir, f'epoch_{result.epoch}.json')
        with open(path, 'w') as f:
            json.dump(result.model_dump(), f, indent=2, default=str)

    # ── Local sim helpers ─────────────────────────────────────────

    def add_miner(self, miner):
        self._local_miners.append(miner)

    # ── On-chain setup ────────────────────────────────────────────

    def setup_chain(self):
        import bittensor as bt
        self._wallet = bt.Wallet(name=self.wallet_name, hotkey=self.hotkey)
        self._subtensor = bt.Subtensor(network=self.network)
        self._metagraph = self._subtensor.metagraph(netuid=self.netuid)
        self._dendrite = bt.dendrite(wallet=self._wallet)

    def run(self, step_time: int = 10):
        if not self.local:
            self.setup_chain()
        print(f"validator running | tempo={self.tempo}s | local={self.local}")
        try:
            while True:
                elapsed = time.time() - self.epoch_time
                if elapsed < self.tempo:
                    time.sleep(min(step_time, self.tempo - elapsed))
                    continue
                try:
                    self.epoch()
                except Exception as e:
                    print(f"  epoch error: {e}")
                    import traceback; traceback.print_exc()
        except KeyboardInterrupt:
            print("validator stopped")

    # ── Results ───────────────────────────────────────────────────

    def leaderboard(self) -> List[Dict]:
        return self.incentive.get_leaderboard()

    def results(self, epoch: int = None) -> Optional[Dict]:
        if epoch is None:
            epoch = self.epochs
        path = os.path.join(self.storage_path, 'epochs', f'epoch_{epoch}.json')
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return None
