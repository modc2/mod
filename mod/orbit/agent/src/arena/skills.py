"""skills - named bundles of tasks with a composite leaderboard

A skill groups related tasks together and ranks agents by their weighted
average performance across all the tasks in the skill. The creator sets the
task list; all weights default to 1.0 and can be tuned per task.

    coding_skill = {
        "id": "a1b2c3d4",
        "name": "Coding",
        "tasks": [
            {"key": "arena#addup@0", "weight": 1.0},
            {"key": "code/python#3", "weight": 2.0},
        ],
    }

Leaderboard computation:
  - A task not played by an agent is excluded from both numerator and
    denominator (partial coverage is honest, not penalised).
  - weighted_score = sum(last_score * weight for played tasks) /
                     sum(weight for played tasks)
  - Agents that have not played any task in the skill are excluded entirely.

Best model per skill is read off the match log filtered to the skill's tasks.
Best agent design per skill is the same leaderboard keyed by agent identity
(prompt + toolbox + model), surfacing which prompt/memory combination won.
"""
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now() -> float:
    return time.time()


class Skills:
    """Persistent skill registry, stored in skills.json beside state.json."""

    def __init__(self, root: Path):
        self.path = root / "skills.json"
        self._data: Dict[str, Dict] = {}
        self._load()

    def _load(self):
        try:
            self._data = json.loads(self.path.read_text())
        except Exception:
            self._data = {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, indent=2))
        tmp.rename(self.path)

    def list(self) -> List[Dict[str, Any]]:
        out = list(self._data.values())
        out.sort(key=lambda s: s.get("created", 0))
        return out

    def get(self, skill_id: str) -> Optional[Dict[str, Any]]:
        return self._data.get(skill_id)

    def create(self, name: str, description: str = "",
               tasks: List[Any] = None, owner: str = "") -> Dict[str, Any]:
        if not name or not name.strip():
            raise ValueError("skill name is required")
        skill_id = uuid.uuid4().hex[:8]
        skill = {
            "id": skill_id,
            "name": name.strip(),
            "description": description or "",
            "owner": owner or "",
            "tasks": _normalize_tasks(tasks or []),
            "created": _now(),
            "updated": _now(),
        }
        self._data[skill_id] = skill
        self._save()
        return skill

    def update(self, skill_id: str, name: str = None, description: str = None,
               tasks: List[Any] = None, owner: str = None) -> Optional[Dict[str, Any]]:
        skill = self._data.get(skill_id)
        if not skill:
            return None
        if name is not None:
            skill["name"] = name.strip()
        if description is not None:
            skill["description"] = description
        if tasks is not None:
            skill["tasks"] = _normalize_tasks(tasks)
        if owner is not None:
            skill["owner"] = owner
        skill["updated"] = _now()
        self._save()
        return skill

    def remove(self, skill_id: str) -> bool:
        if skill_id in self._data:
            del self._data[skill_id]
            self._save()
            return True
        return False


def _normalize_tasks(tasks: List[Any]) -> List[Dict[str, Any]]:
    out = []
    for t in tasks:
        if isinstance(t, str):
            out.append({"key": t, "weight": 1.0})
        elif isinstance(t, dict):
            key = str(t.get("key") or "").strip()
            if not key:
                continue
            out.append({"key": key, "weight": float(t.get("weight", 1.0))})
    return out


def skill_leaderboard(skill: Dict[str, Any],
                      ratings: Dict[str, Dict[str, Any]],
                      agents_meta: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    """Composite skill leaderboard from per-agent rating data.

    Args:
        skill: the skill dict with tasks list
        ratings: agent → rating record (contains per_task, elo, matches, etc.)
        agents_meta: optional agent → {icon, model, system, ...} for surfacing
                     the best prompt/memory/model combination
    """
    task_weights = {t["key"]: float(t.get("weight", 1.0))
                    for t in skill.get("tasks", [])}
    if not task_weights:
        return []

    rows = []
    for agent, r in ratings.items():
        per_task = r.get("per_task") or {}
        score_sum = 0.0
        weight_sum = 0.0
        tasks_played = []
        for key, weight in task_weights.items():
            rec = per_task.get(key)
            if rec is not None:
                score_sum += float(rec.get("last", 0.0)) * weight
                weight_sum += weight
                tasks_played.append(key)
        if weight_sum == 0:
            continue
        meta = (agents_meta or {}).get(agent) or {}
        rows.append({
            "agent": agent,
            "icon": meta.get("icon", ">_"),
            "model": meta.get("model", ""),
            "system": (meta.get("system") or "")[:120],
            "weighted_score": round(score_sum / weight_sum, 4),
            "tasks_played": len(tasks_played),
            "tasks_total": len(task_weights),
            "coverage": round(len(tasks_played) / len(task_weights), 3),
            "elo": round(float(r.get("elo", 1200.0)), 1),
            "matches": int(r.get("matches", 0)),
            "avg_score": round(float(r.get("score_sum", 0.0))
                               / max(1, int(r.get("matches", 0))), 4),
        })
    rows.sort(key=lambda x: (-x["weighted_score"], -x["elo"], x["agent"]))
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


def skill_model_leaderboard(skill: Dict[str, Any],
                             matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Best model per skill: aggregate match scores by model across skill tasks.

    Reads the raw match log. Each match carries model, task and score. Returns
    models ranked by weighted average score on the skill's tasks.
    """
    task_weights = {t["key"]: float(t.get("weight", 1.0))
                    for t in skill.get("tasks", [])}
    if not task_weights:
        return []

    agg: Dict[str, Dict[str, Any]] = {}
    for m in matches:
        if m.get("void"):
            continue
        task = m.get("task", "")
        if task not in task_weights:
            continue
        model = (m.get("model") or "unknown").strip() or "unknown"
        weight = task_weights[task]
        score = float(m.get("score", 0.0))
        rec = agg.setdefault(model, {
            "model": model,
            "score_sum": 0.0,
            "weight_sum": 0.0,
            "matches": 0,
            "tasks": set(),
        })
        rec["score_sum"] += score * weight
        rec["weight_sum"] += weight
        rec["matches"] += 1
        rec["tasks"].add(task)

    rows = []
    for rec in agg.values():
        if rec["weight_sum"] == 0:
            continue
        rows.append({
            "model": rec["model"],
            "weighted_score": round(rec["score_sum"] / rec["weight_sum"], 4),
            "matches": rec["matches"],
            "tasks_played": len(rec["tasks"]),
            "tasks_total": len(task_weights),
            "coverage": round(len(rec["tasks"]) / len(task_weights), 3),
        })
    rows.sort(key=lambda x: (-x["weighted_score"], -x["matches"]))
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


def skill_summary(skill: Dict[str, Any],
                  leaderboard: List[Dict[str, Any]],
                  model_board: List[Dict[str, Any]]) -> Dict[str, Any]:
    """A quick summary card for a skill: the best agent, model and prompt."""
    best_agent = leaderboard[0] if leaderboard else None
    best_model = model_board[0] if model_board else None
    return {
        **skill,
        "participants": len(leaderboard),
        "best_agent": best_agent["agent"] if best_agent else None,
        "best_agent_score": best_agent["weighted_score"] if best_agent else None,
        "best_model": best_model["model"] if best_model else None,
        "best_model_score": best_model["weighted_score"] if best_model else None,
        "best_prompt": best_agent["system"] if best_agent else None,
    }
