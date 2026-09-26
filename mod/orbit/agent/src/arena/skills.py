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

One level up sits a CLASS: a named bundle of skills, each with a weight of
its own, so scores cumulate task -> skill -> class. A class is scored the
same honest way a skill is — a skill the agent has no coverage in is left
out of both sides of the division, never counted as a zero.

Finding what to bundle is `search_tasks`: the task pool ranked against a
plain-language query by the module's own BM25-shaped retrieval engine
(src/memory/retrieval.py) — dependency-free and entirely local, no
embedding service anywhere near it.
"""
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from src.memory import retrieval
except ImportError:  # running the arena standalone
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.memory import retrieval


def _now() -> float:
    return time.time()


class Bundles:
    """A persistent registry of named, weighted bundles.

    Skills (bundles of tasks) and classes (bundles of skills) are the same
    shape — a name, a description, an owner, and a list of weighted members —
    so the registry is written once. Subclasses pick the file, the member
    field and the field a member is identified by.
    """

    filename = "bundles.json"
    member_key = "items"       # the field holding the weighted member list
    id_field = "key"           # the field identifying one member
    noun = "bundle"            # what one of these is called in errors

    def __init__(self, root: Path):
        self.path = root / self.filename
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
               members: List[Any] = None, owner: str = "") -> Dict[str, Any]:
        if not name or not name.strip():
            raise ValueError(f"{self.noun} name is required")
        bundle_id = uuid.uuid4().hex[:8]
        bundle = {
            "id": bundle_id,
            "name": name.strip(),
            "description": description or "",
            "owner": owner or "",
            self.member_key: self._normalize(members or []),
            "created": _now(),
            "updated": _now(),
        }
        self._data[bundle_id] = bundle
        self._save()
        return bundle

    def update(self, bundle_id: str, name: str = None, description: str = None,
               members: List[Any] = None, owner: str = None) -> Optional[Dict[str, Any]]:
        bundle = self._data.get(bundle_id)
        if not bundle:
            return None
        if name is not None:
            bundle["name"] = name.strip()
        if description is not None:
            bundle["description"] = description
        if members is not None:
            bundle[self.member_key] = self._normalize(members)
        if owner is not None:
            bundle["owner"] = owner
        bundle["updated"] = _now()
        self._save()
        return bundle

    def remove(self, bundle_id: str) -> bool:
        if bundle_id in self._data:
            del self._data[bundle_id]
            self._save()
            return True
        return False

    @classmethod
    def _normalize(cls, members: List[Any]) -> List[Dict[str, Any]]:
        out = []
        for m in members:
            if isinstance(m, str):
                out.append({cls.id_field: m, "weight": 1.0})
            elif isinstance(m, dict):
                ident = str(m.get(cls.id_field) or "").strip()
                if not ident:
                    continue
                out.append({cls.id_field: ident,
                            "weight": float(m.get("weight", 1.0))})
        return out


class Skills(Bundles):
    """Persistent skill registry, stored in skills.json beside state.json."""

    filename = "skills.json"
    member_key = "tasks"
    id_field = "key"
    noun = "skill"

    def create(self, name: str, description: str = "",
               tasks: List[Any] = None, owner: str = "") -> Dict[str, Any]:
        return super().create(name, description=description,
                              members=tasks, owner=owner)

    def update(self, skill_id: str, name: str = None, description: str = None,
               tasks: List[Any] = None, owner: str = None) -> Optional[Dict[str, Any]]:
        return super().update(skill_id, name=name, description=description,
                              members=tasks, owner=owner)


class Classes(Bundles):
    """Persistent class registry — bundles of skills, in classes.json."""

    filename = "classes.json"
    member_key = "skills"
    id_field = "id"
    noun = "class"

    def create(self, name: str, description: str = "",
               skills: List[Any] = None, owner: str = "") -> Dict[str, Any]:
        return super().create(name, description=description,
                              members=skills, owner=owner)

    def update(self, class_id: str, name: str = None, description: str = None,
               skills: List[Any] = None, owner: str = None) -> Optional[Dict[str, Any]]:
        return super().update(class_id, name=name, description=description,
                              members=skills, owner=owner)


# ── finding what to bundle ───────────────────────────────────────────

def _task_text(task: Dict[str, Any]) -> str:
    """Everything about a task worth matching a query against: what it is
    called, what it asks for, what it checks, and what it seeds."""
    parts = [task.get("title"), task.get("suite"), task.get("key"),
             task.get("description"), task.get("prompt")]
    for s in task.get("scorers") or []:
        for f in ("path", "text", "pattern", "name", "task", "language"):
            parts.append(s.get(f) if isinstance(s, dict) else None)
    files = (task.get("setup") or {}).get("files") or {}
    parts.extend(files.keys())
    oa = task.get("openarena") or {}
    parts.extend(oa.get("tags") or [])
    parts.append(oa.get("language"))
    return " ".join(str(p) for p in parts if p)


def search_tasks(query: str, tasks: List[Dict[str, Any]],
                 k: int = 20, min_score: float = 0.02) -> List[Dict[str, Any]]:
    """The task pool ranked against a plain-language query, best first.

    Scoring is the memory subsystem's BM25-lite (idf + saturation + length
    norm, normalised 0..1) over the whole spec — title, prompt, checks,
    fixture filenames, tags — so "refactor a python file" finds the tasks
    about that whether or not they share the exact words in their titles.
    Local and dependency-free; nothing leaves the box.
    """
    hits = retrieval.rank(query, tasks, text_of=_task_text,
                          ts_of=lambda t: t.get("updated"),
                          k=k, min_score=min_score)
    out = []
    for score, t in hits:
        out.append({
            "score": round(float(score), 3),
            "key": t.get("key"),
            "suite": t.get("suite"),
            "title": t.get("title"),
            "prompt": (t.get("prompt") or "")[:240],
            "checks": len(t.get("scorers") or []),
            "custom": bool(t.get("custom")),
            "owner": t.get("owner"),
        })
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


# ── classes: skills rolled up one more level ─────────────────────────

def _class_members(cls: Dict[str, Any],
                   skills_by_id: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The class's member skills that still exist, with their weights."""
    out = []
    for m in cls.get("skills", []):
        skill = skills_by_id.get(m.get("id"))
        if skill:
            out.append({"skill": skill, "weight": float(m.get("weight", 1.0))})
    return out


def class_leaderboard(cls: Dict[str, Any],
                      skills_by_id: Dict[str, Dict[str, Any]],
                      ratings: Dict[str, Dict[str, Any]],
                      agents_meta: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    """The class benchmark: each skill's weighted score, rolled up again.

    An agent's class score is the weighted mean of its skill scores over the
    skills it has any coverage in — same honesty rule as inside a skill: a
    skill never played is left out of both sides of the division, and an
    agent with no coverage anywhere is not on the board.
    """
    members = _class_members(cls, skills_by_id)
    if not members:
        return []

    boards = [(m["skill"], m["weight"],
               {row["agent"]: row
                for row in skill_leaderboard(m["skill"], ratings, agents_meta)})
              for m in members]

    agents = {a for _, _, board in boards for a in board}
    rows = []
    for agent in agents:
        score_sum = 0.0
        weight_sum = 0.0
        per_skill = []
        for skill, weight, board in boards:
            row = board.get(agent)
            per_skill.append({
                "id": skill["id"],
                "name": skill["name"],
                "weight": weight,
                "score": row["weighted_score"] if row else None,
                "coverage": row["coverage"] if row else 0.0,
            })
            if row is not None:
                score_sum += row["weighted_score"] * weight
                weight_sum += weight
        if weight_sum == 0:
            continue
        meta = (agents_meta or {}).get(agent) or {}
        r = ratings.get(agent) or {}
        rows.append({
            "agent": agent,
            "icon": meta.get("icon", ">_"),
            "model": meta.get("model", ""),
            "weighted_score": round(score_sum / weight_sum, 4),
            "skills_played": sum(1 for s in per_skill if s["score"] is not None),
            "skills_total": len(members),
            "coverage": round(sum(1 for s in per_skill if s["score"] is not None)
                              / len(members), 3),
            "per_skill": per_skill,
            "elo": round(float(r.get("elo", 1200.0)), 1),
            "matches": int(r.get("matches", 0)),
        })
    rows.sort(key=lambda x: (-x["weighted_score"], -x["elo"], x["agent"]))
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


def class_model_leaderboard(cls: Dict[str, Any],
                            skills_by_id: Dict[str, Dict[str, Any]],
                            matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Best model per class: each skill's model board, rolled up by weight."""
    members = _class_members(cls, skills_by_id)
    if not members:
        return []
    agg: Dict[str, Dict[str, Any]] = {}
    for m in members:
        for row in skill_model_leaderboard(m["skill"], matches):
            rec = agg.setdefault(row["model"], {
                "model": row["model"], "score_sum": 0.0, "weight_sum": 0.0,
                "matches": 0, "skills": 0,
            })
            rec["score_sum"] += row["weighted_score"] * m["weight"]
            rec["weight_sum"] += m["weight"]
            rec["matches"] += row["matches"]
            rec["skills"] += 1
    rows = []
    for rec in agg.values():
        if rec["weight_sum"] == 0:
            continue
        rows.append({
            "model": rec["model"],
            "weighted_score": round(rec["score_sum"] / rec["weight_sum"], 4),
            "matches": rec["matches"],
            "skills_played": rec["skills"],
            "skills_total": len(members),
            "coverage": round(rec["skills"] / len(members), 3),
        })
    rows.sort(key=lambda x: (-x["weighted_score"], -x["matches"]))
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


def class_summary(cls: Dict[str, Any],
                  leaderboard: List[Dict[str, Any]],
                  model_board: List[Dict[str, Any]]) -> Dict[str, Any]:
    """A quick summary card for a class: the best agent and model across it."""
    best_agent = leaderboard[0] if leaderboard else None
    best_model = model_board[0] if model_board else None
    return {
        **cls,
        "participants": len(leaderboard),
        "best_agent": best_agent["agent"] if best_agent else None,
        "best_agent_score": best_agent["weighted_score"] if best_agent else None,
        "best_model": best_model["model"] if best_model else None,
        "best_model_score": best_model["weighted_score"] if best_model else None,
    }
