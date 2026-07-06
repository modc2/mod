import hashlib
import time
import random
import mod as m
from core.schemas import Challenge, ChallengeType

VERTICALS = [
    "AI + healthcare", "developer tools", "fintech for emerging markets",
    "defense tech", "climate + energy", "AI + education", "biotech",
    "space tech", "cybersecurity", "real estate tech", "supply chain",
    "food + agriculture", "longevity", "robotics", "creator economy",
    "AI + legal", "construction tech",
]

SEED_CHALLENGES = [
    Challenge(
        id="seed_open_1", type=ChallengeType.OPEN,
        prompt="Pitch a startup that could be in the next YC batch. Any sector, any stage. Be specific and original.",
    ),
    Challenge(
        id="seed_vertical_ai", type=ChallengeType.VERTICAL,
        prompt="Pitch a startup applying AI to an unsexy, overlooked industry.",
        constraints={"sector": "AI"},
    ),
    Challenge(
        id="seed_vertical_climate", type=ChallengeType.VERTICAL,
        prompt="Pitch a climate tech startup that could reach $100M ARR in 5 years.",
        constraints={"sector": "climate"},
    ),
    Challenge(
        id="seed_problem_1", type=ChallengeType.PROBLEM_FIRST,
        prompt="Small businesses waste 20+ hours/month on invoicing and payments. Pitch a startup that solves this.",
        constraints={"problem": "SMB invoicing"},
    ),
    Challenge(
        id="seed_contrarian_1", type=ChallengeType.CONTRARIAN,
        prompt="Pitch a startup based on a contrarian thesis that most smart people would disagree with. Explain why the consensus is wrong.",
    ),
]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class ChallengeGenerator:

    def __init__(self, llm=None, llm_backend='openrouter'):
        self.llm = llm
        self.llm_backend = llm_backend

    def _get_llm(self):
        if self.llm is None:
            self.llm = m.mod(self.llm_backend)()
        return self.llm

    def generate(self, epoch: int = 0, challenge_type=None) -> Challenge:
        if challenge_type is not None:
            if isinstance(challenge_type, str):
                challenge_type = ChallengeType(challenge_type)
        else:
            types = list(ChallengeType)
            challenge_type = types[epoch % len(types)]

        if challenge_type == ChallengeType.OPEN:
            return self._open(epoch)
        elif challenge_type == ChallengeType.VERTICAL:
            return self._vertical(epoch)
        elif challenge_type == ChallengeType.PROBLEM_FIRST:
            return self._problem(epoch)
        elif challenge_type == ChallengeType.CONTRARIAN:
            return self._contrarian(epoch)
        return self._open(epoch)

    def _open(self, epoch: int) -> Challenge:
        prompts = [
            "Pitch a startup that could be in the next YC batch. Any sector, any stage. Be specific and original.",
            "Pitch a B2B SaaS startup solving a problem you've personally experienced. Be concrete.",
            "Pitch a startup that uses a new technology trend to disrupt an existing market.",
            "Pitch a startup targeting a market that doesn't exist yet but will in 3 years.",
        ]
        return Challenge(
            id=_hash(f"open_{epoch}_{time.time()}"),
            type=ChallengeType.OPEN,
            prompt=prompts[epoch % len(prompts)],
            epoch=epoch,
        )

    def _vertical(self, epoch: int) -> Challenge:
        vertical = VERTICALS[epoch % len(VERTICALS)]
        return Challenge(
            id=_hash(f"vertical_{vertical}_{epoch}"),
            type=ChallengeType.VERTICAL,
            prompt=f"Pitch a startup in {vertical}. The idea must be specific, actionable, and fundable at pre-seed.",
            constraints={"sector": vertical},
            epoch=epoch,
        )

    def _problem(self, epoch: int) -> Challenge:
        llm = self._get_llm()
        raw = llm.forward(
            "Generate a specific, real-world problem statement that a startup could solve. "
            "Include a concrete pain point, who suffers from it, and rough scale. "
            "Return ONLY the problem statement in 2-3 sentences.",
            temperature=1.2,
        )
        problem = raw.strip()
        return Challenge(
            id=_hash(f"problem_{epoch}_{time.time()}"),
            type=ChallengeType.PROBLEM_FIRST,
            prompt=f"A real problem: {problem}\n\nPitch a startup that solves this.",
            constraints={"generated_problem": problem},
            epoch=epoch,
        )

    def _contrarian(self, epoch: int) -> Challenge:
        return Challenge(
            id=_hash(f"contrarian_{epoch}_{time.time()}"),
            type=ChallengeType.CONTRARIAN,
            prompt=(
                "Pitch a startup based on a contrarian thesis. Your idea should be something "
                "most VCs and smart people would initially reject. Explain WHY the consensus is wrong "
                "and why you're right."
            ),
            epoch=epoch,
        )

    def from_seed(self, index: int = None) -> Challenge:
        if index is not None:
            return SEED_CHALLENGES[index % len(SEED_CHALLENGES)]
        return random.choice(SEED_CHALLENGES)
