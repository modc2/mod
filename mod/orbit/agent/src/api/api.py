"""
agent api - thin FastAPI gateway over mod.forward()

All logic lives in agent/mod.py. The API just dispatches to forward().

Endpoints:
    GET  /health       - health check
    GET  /status       - module status
    GET  /skills       - list skills + schemas
    GET  /schema       - get skill schemas for LLM
    GET  /agents       - list agent personas
    GET  /agents/{name} - get agent config
    GET  /chains       - list chain presets
    GET  /library      - unified index: prompts + skills + memory + agents (q/kind/tag filters)
    GET  /prompts      - prompt library      POST /prompts  DELETE /prompts/{id}
    GET  /memory       - memory notes        POST /memory   DELETE /memory/{id}
    POST /forward      - mod protocol entry point
    POST /run          - run the full agent loop
    POST /skills/run   - run a single skill

Usage:
    uvicorn api:app --host 0.0.0.0 --port 50117 --reload
"""
import os
import sys
from typing import Optional, List
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# resolve paths: api.py is at src/api/api.py
# paths MUST be normalized — the mod framework prefix-matches sys.path entries
# against absolute paths, and an unnormalized entry silently breaks its import
src_dir = os.path.join(os.path.dirname(__file__), '..')             # src/
module_root = os.path.abspath(os.path.join(src_dir, '..'))          # orbit/agent/
mod_root = os.path.abspath(os.path.join(module_root, '..', '..', '..'))  # mod framework root
sys.path.insert(0, module_root)
sys.path.insert(0, mod_root)

app = FastAPI(title="Agent API", version="3.2.0", description="Autonomous coding agent API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── request models ───────────────────────────────────────────────────

class ForwardRequest(BaseModel):
    action: Optional[str] = None
    params: dict = {}
    key: Optional[str] = None

class RunRequest(BaseModel):
    query: str
    model: str = "anthropic/claude-sonnet-4.5"
    provider: Optional[str] = None
    steps: int = 10
    skills: Optional[List[str]] = None
    temperature: float = 0.0
    safety: bool = False
    free: bool = False
    agent: Optional[str] = None
    agent_type: Optional[str] = None
    chain: Optional[List[dict]] = None
    key: Optional[str] = None

class SkillRunRequest(BaseModel):
    name: str
    params: dict = {}
    key: Optional[str] = None

class AgentCreateRequest(BaseModel):
    name: str
    description: str = ""
    goal: str = ""
    icon: str = ">_"
    skills: Optional[List[str]] = None
    model: Optional[str] = None
    key: Optional[str] = None

class GrantRequest(BaseModel):
    address: str
    actions: Optional[List[str]] = None  # default: ['run', 'skill']
    key: Optional[str] = None  # owner auth token

class RevokeRequest(BaseModel):
    address: str
    key: Optional[str] = None

class AgentRegisterRequest(BaseModel):
    name: str
    backend: str = "offchain"
    key: Optional[str] = None

class PromptRequest(BaseModel):
    name: str
    text: str
    description: str = ""
    tags: Optional[List[str]] = None
    id: Optional[str] = None
    key: Optional[str] = None

class MemoryNoteRequest(BaseModel):
    name: str
    content: str
    tags: Optional[List[str]] = None
    id: Optional[str] = None
    key: Optional[str] = None


# ── lazy mod singleton ───────────────────────────────────────────────

_mod = None

def get_mod():
    global _mod
    if _mod is None:
        from src.mod import Mod
        _mod = Mod()
    return _mod


# ── routes (thin wrappers over mod.forward) ──────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "module": "agent", "version": "3.2.0"}

@app.get("/config")
def get_config():
    """Get module config.json"""
    import json
    config_path = os.path.join(module_root, 'config.json')
    if not os.path.exists(config_path):
        return {"error": "config.json not found"}
    with open(config_path) as f:
        return json.load(f)

@app.get("/status")
def get_status():
    return get_mod().forward('status')

@app.post("/forward")
def forward(req: ForwardRequest):
    """Mod protocol entry point: dispatch any action"""
    mod = get_mod()
    try:
        result = mod.forward(action=req.action, key=req.key, **req.params)
        return {"action": req.action, "result": result}
    except PermissionError as e:
        return {"action": req.action, "error": str(e), "code": 403}
    except Exception as e:
        return {"action": req.action, "error": str(e)}

@app.get("/skills")
def list_skills():
    mod = get_mod()
    return {"skills": mod.skills.ls(), "schemas": mod.skill_schema()}

@app.get("/schema")
def get_schema():
    return get_mod().skill_schema()

@app.get("/providers")
def list_providers():
    """List LLM providers with their selectable models and default model."""
    mod = get_mod()
    providers = []
    for key in mod.PROVIDERS:
        default_model = mod.DEFAULT_MODELS.get(key) or mod.DEFAULT_MODELS.get(mod.PROVIDERS.get(key, ''), '')
        providers.append({
            "key": key,
            "models": mod.MODELS.get(key, []),
            "default_model": default_model,
        })
    return {"providers": providers, "default": "openrouter"}

@app.post("/skills/run")
def run_skill(req: SkillRunRequest):
    """Run a single skill. Write skills are path-restricted for non-owners."""
    mod = get_mod()
    try:
        if req.name in ('write', 'edit', 'patch'):
            allowed = mod.allowed_paths_for(req.key)
            fp = req.params.get('file_path', '')
            if fp and allowed is not None:
                from src.mod import check_path_allowed
                if not check_path_allowed(fp, allowed):
                    return {"skill": req.name, "error": f"Permission denied: cannot write to {fp}", "code": 403}
        result = mod.run_skill(req.name, **req.params)
        return {"skill": req.name, "result": result}
    except KeyError:
        return {"skill": req.name, "error": f"unknown skill: {req.name}", "available": mod.skills.ls()}
    except Exception as e:
        return {"skill": req.name, "error": str(e)}

@app.get("/owner")
def get_owner():
    mod = get_mod()
    return {"owner": mod._owner, "has_owner": bool(mod._owner)}

# ── access control (gate) ────────────────────────────────────────────

@app.post("/grant")
def grant_access(req: GrantRequest):
    """Grant admin access to an address. Owner only."""
    try:
        return get_mod().forward('grant', key=req.key, address=req.address, actions=req.actions)
    except PermissionError as e:
        return {"error": str(e), "code": 403}

@app.post("/revoke")
def revoke_access(req: RevokeRequest):
    """Revoke access from an address. Owner only."""
    try:
        return get_mod().forward('revoke', key=req.key, address=req.address)
    except PermissionError as e:
        return {"error": str(e), "code": 403}

@app.get("/acl")
def get_acl(key: Optional[str] = None):
    """View current access control list. Owner only."""
    try:
        return get_mod().forward('acl', key=key)
    except PermissionError as e:
        return {"error": str(e), "code": 403}

# ── agents (from agents/ registry) ──────────────────────────────────

@app.get("/agents")
def list_agents():
    """List all agent personas from agents/ directory"""
    return get_mod().forward('agents')

@app.get("/agents/{name}")
def get_agent(name: str):
    """Get a specific agent config"""
    try:
        config = get_mod().forward('agent', name=name)
        return {k: v for k, v in config.items() if k != 'cls'}
    except KeyError:
        return {"error": f"agent not found: {name}", "available": get_mod().agents.ls()}

@app.post("/agents")
def create_agent(req: AgentCreateRequest):
    """Create a new agent locally"""
    mod = get_mod()
    try:
        result = mod.forward('agents', action='create',
            name=req.name, description=req.description, goal=req.goal,
            icon=req.icon, skills=req.skills, model=req.model, key=req.key)
        return result
    except (FileExistsError, PermissionError) as e:
        return {"error": str(e)}

@app.delete("/agents/{name}")
def remove_agent(name: str, key: Optional[str] = None):
    """Remove a local agent"""
    mod = get_mod()
    try:
        return mod.forward('agents', action='remove', name=name, key=key)
    except (KeyError, PermissionError) as e:
        return {"error": str(e)}

@app.post("/agents/{name}/register")
def register_agent(name: str, req: AgentRegisterRequest):
    """Register an agent on the registry (offchain or on-chain)"""
    mod = get_mod()
    try:
        return mod.forward('agents', action='register',
            name=name, backend=req.backend, key=req.key)
    except Exception as e:
        return {"error": str(e)}

@app.get("/chains")
def list_chains():
    """List chain presets"""
    return get_mod().forward('chains')

# ── library (prompts / skills / memory / agent market) ──────────────

@app.get("/library")
def get_library(q: Optional[str] = None, kind: Optional[str] = None,
                tag: Optional[str] = None):
    """Unified filterable index across prompts, skills, memory, and agents."""
    return get_mod().library.items(q=q, kind=kind, tag=tag)

@app.get("/prompts")
def list_prompts():
    return {"prompts": get_mod().library.prompts()}

@app.post("/prompts")
def save_prompt(req: PromptRequest):
    """Create or update a prompt (upsert by id)."""
    try:
        return get_mod().library.prompt_add(
            req.name, req.text, req.description, req.tags, req.id)
    except ValueError as e:
        return {"error": str(e)}

@app.delete("/prompts/{prompt_id}")
def delete_prompt(prompt_id: str):
    try:
        return get_mod().library.prompt_rm(prompt_id)
    except KeyError as e:
        return {"error": str(e)}

@app.get("/memory")
def list_memory():
    return {"memory": get_mod().library.notes()}

@app.post("/memory")
def save_memory(req: MemoryNoteRequest):
    """Create or update a memory note (upsert by id)."""
    try:
        return get_mod().library.note_add(req.name, req.content, req.tags, req.id)
    except ValueError as e:
        return {"error": str(e)}

@app.delete("/memory/{note_id}")
def delete_memory(note_id: str):
    try:
        return get_mod().library.note_rm(note_id)
    except KeyError as e:
        return {"error": str(e)}

# ── run (delegates to mod.forward('run')) ────────────────────────────

@app.post("/run")
def run_agent(req: RunRequest):
    """Run the agent loop. Agent resolution happens in Mod._run()."""
    mod = get_mod()
    if mod.model is None:
        return {"error": "No LLM model configured."}

    resolved_agent = req.agent_type or req.agent

    # chain execution
    if req.chain and len(req.chain) > 0:
        chain_results = []
        context = req.query
        for i, step in enumerate(req.chain):
            step_agent = step.get("agent", "default")
            step_prompt = step.get("prompt", "")
            if i == 0:
                step_query = f"{step_prompt}\n\nUser request: {context}" if step_prompt else context
            else:
                prev_summary = chain_results[-1].get("summary", "")
                step_query = f"{step_prompt}\n\nUser request: {context}\n\nPrevious step output: {prev_summary}" if step_prompt else context
            try:
                result = mod.forward('run',
                    key=req.key,
                    query=step_query,
                    model=req.model,
                    provider=req.provider,
                    steps=req.steps,
                    agent_type=step_agent,
                    temperature=req.temperature,
                    safety=req.safety,
                    free=req.free,
                )
                summary = ""
                if isinstance(result, list):
                    for s in result:
                        if isinstance(s, dict) and s.get("tool") == "finish":
                            summary = s.get("params", {}).get("summary", "")
                chain_results.append({"step": i, "agent": step_agent, "result": result, "summary": summary})
            except Exception as e:
                chain_results.append({"step": i, "agent": step_agent, "error": str(e), "summary": f"Error: {e}"})
        return {"query": req.query, "chain": True, "results": chain_results}

    # single agent run
    try:
        result = mod.forward('run',
            key=req.key,
            query=req.query,
            model=req.model,
            provider=req.provider,
            steps=req.steps,
            skills=req.skills,
            agent_type=resolved_agent,
            temperature=req.temperature,
            safety=req.safety,
            free=req.free,
        )
        return {"query": req.query, "agent_type": resolved_agent, "result": result}
    except PermissionError as e:
        return {"query": req.query, "error": str(e), "code": 403}
    except Exception as e:
        return {"query": req.query, "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 50117))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=True)
