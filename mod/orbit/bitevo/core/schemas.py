from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from enum import Enum
import hashlib
import time


class ChallengeType(str, Enum):
    OPEN = "open"
    VERTICAL = "vertical"
    PROBLEM_FIRST = "problem_first"
    CONTRARIAN = "contrarian"


class Challenge(BaseModel):
    id: str = ""
    type: ChallengeType = ChallengeType.OPEN
    prompt: str = ""
    constraints: Dict[str, str] = {}
    created_at: float = Field(default_factory=time.time)
    epoch: int = 0

    def model_post_init(self, __context):
        if not self.id:
            raw = f"{self.type}_{self.prompt}_{self.epoch}_{self.created_at}"
            self.id = hashlib.sha256(raw.encode()).hexdigest()[:16]


class StartupPitch(BaseModel):
    company_name: str = ""
    one_liner: str = ""
    problem: str = ""
    solution: str = ""
    market: str = ""
    traction: str = ""
    business_model: str = ""
    team: str = ""
    defensibility: str = ""
    ask: str = ""
    raw_text: str = ""


class ScoreBreakdown(BaseModel):
    novelty: float = 0.0
    feasibility: float = 0.0
    market_size: float = 0.0
    clarity: float = 0.0
    defensibility: float = 0.0
    traction_signal: float = 0.0


class MinerResponse(BaseModel):
    miner_uid: int = -1
    miner_hotkey: str = ""
    challenge_id: str = ""
    pitch: StartupPitch = Field(default_factory=StartupPitch)
    model_used: str = ""
    generation_time: float = 0.0
    timestamp: float = Field(default_factory=time.time)


class ValidatorScore(BaseModel):
    miner_uid: int = -1
    miner_hotkey: str = ""
    challenge_id: str = ""
    breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    composite_score: float = 0.0
    feedback: str = ""
    timestamp: float = Field(default_factory=time.time)


class EpochResult(BaseModel):
    epoch: int = 0
    challenge: Challenge = Field(default_factory=Challenge)
    responses: List[MinerResponse] = []
    scores: List[ValidatorScore] = []
    weights: Dict[str, float] = {}
    timestamp: float = Field(default_factory=time.time)
