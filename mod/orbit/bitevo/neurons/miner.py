import json
import time
import re
from core.schemas import Challenge, StartupPitch, MinerResponse
from core.prompts import MINER_SYSTEM_PROMPT, MINER_USER_PROMPT
from core.llm import get_llm

DEFAULT_MODELS = {
    'claude': 'haiku',
    'openrouter': 'anthropic/claude-sonnet-4',
    'venice': 'llama-3.3-70b',
    'chutes': 'unsloth/Llama-3.3-70B-Instruct',
}


class BitevoMiner:

    def __init__(
        self,
        backend: str = 'claude',
        model: str = None,
        uid: int = 0,
        hotkey: str = 'default',
        wallet_name: str = 'miner',
        netuid: int = None,
        network: str = 'finney',
        local: bool = True,
        temperature: float = 0.9,
        payout: str = None,
        **kwargs,
    ):
        self.backend = backend
        self.model = model or DEFAULT_MODELS.get(backend, DEFAULT_MODELS['claude'])
        self.uid = uid
        self.hotkey = hotkey
        self.wallet_name = wallet_name
        self.netuid = netuid
        self.network = network
        self.local = local
        self.temperature = temperature
        self._llm = None
        # zuma identity: a ring key registered as a miner; payout is where
        # claims are credited (the miner's choice — it is never linked to a tag)
        self.zuma_key = f'miner-{uid}'
        self.payout = payout or self.zuma_key

    @property
    def llm(self):
        if self._llm is None:
            self._llm = get_llm(self.backend, model=self.model)
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

    # -- zuma: anonymous submit + claim --

    def zuma_register(self, subnet):
        return subnet.keygen(self.zuma_key, 'miner')

    def zuma_submit(self, subnet, epoch: int, response: MinerResponse) -> MinerResponse:
        """Strip identity, commit to the pitch and ring-sign the commitment in
        the epoch mine scope. The key image is this epoch's tag."""
        from core import zuma
        work = zuma.commit(response.pitch.model_dump())
        sig = zuma.sign(subnet.ring('miner'), subnet.secret(self.zuma_key),
                        zuma.mine_scope(subnet.subnet, epoch), work)
        return MinerResponse(
            challenge_id=response.challenge_id, pitch=response.pitch,
            generation_time=response.generation_time,
            tag=sig['tag'], work=hex(work), zuma_sig=sig,
        )

    def zuma_tag(self, subnet, epoch: int) -> str:
        """Own tag for an epoch — only the key holder can compute it."""
        from core import zuma
        img = zuma.key_image(subnet.secret(self.zuma_key),
                             zuma.mine_scope(subnet.subnet, epoch))
        return hex(zuma.key_id(img))

    def zuma_claim(self, subnet, epoch: int):
        """Re-sign the closed epoch's mine scope (same image = same tag) with
        msg = payout; the subnet credits the payout with this tag's share."""
        from core import zuma
        sig = zuma.sign(subnet.ring('miner'), subnet.secret(self.zuma_key),
                        zuma.mine_scope(subnet.subnet, epoch),
                        zuma.felt(self.payout))
        return subnet.claim(epoch, self.payout, sig)

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
