"""Shared data model for the interaction-cost engine.

This is the ONLY module the interaction-engine workers share. It defines the
user-behavior graph, the UI target geometry, and the HCI operator constants.
Everything else (cost_model, user_model, ui_map, engine) imports from here so
the pieces can be built and tested independently and in parallel.

Concept
-------
We model a jcode-mobile user as a weighted directed graph (a Markov chain over
UI states). Nodes are UI states the user can be in; edges are actions with a
relative `weight` = how likely a user in that state is to take that action.
Normalizing the out-edges of a state gives transition probabilities. The
stationary distribution of that chain tells us how often each state is visited;
multiplying by the per-action cost (predicted in SECONDS from KLM/TLM + Fitts)
gives an expected interaction cost we can optimize against.

HCI grounding (seconds)
-----------------------
KLM (Card, Moran & Newell 1983) + Touch-Level Model (Rice & Lartigue 2014):
  M  mental act / decision .......... 1.35 s
  TAP discrete touch/button press ... 0.20 s   (KLM K for avg typist ~0.20)
  H  homing / reposition hand ....... 0.40 s
  K  keystroke (avg non-secretary) .. 0.28 s
  R  system response (set per action; e.g. sheet present, network round-trip)
Fitts' law (touch): MT = a + b * log2(D/W + 1), a~0.0 s, b~0.20 s/bit.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Operators:
    """KLM/TLM operator times in seconds + Fitts constants. Literature-grounded
    defaults; a benchmarking harness may override after empirical calibration."""
    M: float = 1.35        # mental act / decision
    TAP: float = 0.20      # discrete tap / button press
    H: float = 0.40        # homing / hand reposition
    K: float = 0.28        # single keystroke (avg non-secretary typist)
    FITTS_A: float = 0.0   # Fitts intercept (s)
    FITTS_B: float = 0.20  # Fitts slope (s/bit)


@dataclass
class UITarget:
    """Geometry of a tappable control, in points (44pt is Apple's HIG minimum).
    x_pt/y_pt are the target CENTER; used for Fitts movement-time + reachability."""
    id: str
    width_pt: float
    height_pt: float
    x_pt: float
    y_pt: float
    exists: bool = True   # False => the control is absent in the current build


@dataclass
class UserState:
    """A node: a UI context the user can be in (e.g. 'chat', 'settings_sheet')."""
    id: str
    label: str
    screen: str           # which SwiftUI view renders this state


@dataclass
class Action:
    """An edge: a single user action moving from `src` state to `dst` state.

    weight     relative likelihood a user in `src` takes this action (unnormalized;
               engine normalizes out-edges per state into probabilities).
    target_id  the UITarget tapped, if any (drives Fitts movement time).
    operators  KLM/TLM operator letters performed, e.g. ["M","TAP"] or ["K"]*n.
    response_s extra system/network wait in seconds (sheet present, reconnect).
    """
    id: str
    label: str
    src: str
    dst: str
    weight: float
    target_id: str | None = None
    operators: list[str] = field(default_factory=list)
    response_s: float = 0.0


@dataclass
class Task:
    """A canonical user goal expressed as an ordered list of action ids. The
    engine sums per-action times to get a task-completion time in seconds, and
    weights tasks by `frequency` (relative how-often-per-session)."""
    id: str
    label: str
    action_ids: list[str]
    frequency: float


@dataclass
class ActionGraph:
    """The full user-behavior model."""
    states: dict[str, UserState]
    actions: dict[str, Action]
    tasks: list[Task]
    start: str

    def out_edges(self, state_id: str) -> list[Action]:
        return [a for a in self.actions.values() if a.src == state_id]


@dataclass
class CostBreakdown:
    """Result of pricing one action: total seconds + per-operator detail."""
    action_id: str
    seconds: float
    detail: dict[str, float] = field(default_factory=dict)
