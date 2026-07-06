import json
import time
import re
import mod as m
from core.schemas import Challenge, StartupPitch, MinerResponse
from core.prompts import MINER_SYSTEM_PROMPT, MINER_USER_PROMPT

DEFAULT_MODELS = {
    'openrouter': 'anthropic/claude-sonnet-4',
    'venice': 'llama-3.3-70b',
    'chutes': 'unsloth/Llama-3.3-70B-Instruct',
}


class BitevoMiner:

    def __init__(
        self,
        backend: str = 'openrouter',
        model: str = None,
        uid: int = 0,
        hotkey: str = 'default',
        wallet_name: str = 'miner',
        netuid: int = None,
        network: str = 'finney',
        local: bool = True,
        temperature: float = 0.9,
        **kwargs,
    ):
        self.backend = backend
        self.model = model or DEFAULT_MODELS.get(backend, 'anthropic/claude-sonnet-4')
        self.uid = uid
        self.hotkey = hotkey
        self.wallet_name = wallet_name
        self.netuid = netuid
        self.network = network
        self.local = local
        self.temperature = temperature
        self._llm = None

    @property
    def llm(self):
        if self._llm is None:
            self._llm = m.mod(self.backend)()
        return self._llm

    def forward(self, challenge: Challenge, **kwargs) -> MinerResponse:
        t0 = time.time()

        constraints_text = ""
        if challenge.constraints:
            constraints_text = "Constraints: " + ", ".join(
                f"{k}: {v}" for k, v in challenge.constraints.items()
                if k != "generated_problem"
            )

        user_prompt = MINER_USER_PROMPT.format(
            challenge_prompt=challenge.prompt,
            constraints_text=constraints_text,
        )

        raw = self.llm.forward(
            user_prompt,
            system_prompt=MINER_SYSTEM_PROMPT,
            model=self.model,
            temperature=self.temperature,
            stream=False,
        )

        pitch = self._parse_pitch(raw)

        return MinerResponse(
            miner_uid=self.uid,
            miner_hotkey=self.hotkey,
            challenge_id=challenge.id,
            pitch=pitch,
            model_used=f"{self.backend}/{self.model}",
            generation_time=round(time.time() - t0, 2),
        )

    def _parse_pitch(self, raw: str) -> StartupPitch:
        text = raw.strip()
        # strip markdown code fences
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if "```" in text:
                text = text[:text.rfind("```")]
        try:
            data = json.loads(text.strip())
            return StartupPitch(**data, raw_text=raw)
        except (json.JSONDecodeError, TypeError):
            pass

        # fallback: extract fields
        return StartupPitch(
            company_name=self._extract(raw, "company_name") or "Unknown",
            one_liner=self._extract(raw, "one_liner") or "",
            problem=self._extract(raw, "problem") or "",
            solution=self._extract(raw, "solution") or "",
            market=self._extract(raw, "market") or "",
            traction=self._extract(raw, "traction") or "",
            business_model=self._extract(raw, "business_model") or "",
            team=self._extract(raw, "team") or "",
            defensibility=self._extract(raw, "defensibility") or "",
            ask=self._extract(raw, "ask") or "",
            raw_text=raw,
        )

    def _extract(self, text: str, field: str) -> str:
        patterns = [
            rf'"{field}"\s*:\s*"((?:[^"\\]|\\.)*)"',
            rf'{field}[:\-]\s*(.*?)(?:\n|$)',
        ]
        for p in patterns:
            match = re.search(p, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return ""

    # -- On-chain stubs --

    def setup_chain(self):
        import bittensor as bt
        self._wallet = bt.Wallet(name=self.wallet_name, hotkey=self.hotkey)
        self._subtensor = bt.Subtensor(network=self.network)
        self._axon = bt.axon(wallet=self._wallet)
        self._axon.attach(self.forward)

    def run(self):
        if self.local:
            raise RuntimeError("Cannot run on-chain loop in local mode")
        self.setup_chain()
        self._axon.serve(netuid=self.netuid, subtensor=self._subtensor)
        self._axon.start()
        print(f"Miner axon serving on netuid={self.netuid}")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            self._axon.stop()
