"""
agents - agent registry

Auto-discovers agent modules in agents/ directory.
Each agent is a folder with mod.py containing an Agent class.
Create new agents locally or register on-chain via the registry module.
Save agents as JSON CIDs (localfs content-addressed storage).

Usage:
    agents = Agents()
    agents.ls()                         # list all agent names
    agents.get("architect")             # get agent config
    agents.create("myagent", {...})     # create new agent locally
    agents.save("architect")            # save agent as JSON CID -> "localfs/Qm..."
    agents.load("localfs/Qm...")        # load agent from CID
    agents.register("myagent", key=k)   # register on-chain
    agents.forward("architect")         # mod protocol entry point
"""
import os
import json
import time
import secrets
import importlib.util
from pathlib import Path
from typing import Dict, Any, List, Optional, Union

try:
    import mod as m
except ImportError:
    m = None

AGENT_TEMPLATE = '''"""{name} agent - {description}"""


class Agent:
    name = "{label}"
    description = "{description}"
    icon = "{icon}"
    skills = {skills}
    model = {model}

    goal = """{goal}"""
'''


class Agents:
    description = "Agent registry - discover, load, create, and register agent personas"

    def __init__(self, **kwargs):
        self._dir = Path(__file__).parent
        self._cache = {}

    def ls(self) -> List[str]:
        """List available agent names"""
        return sorted([
            d.name for d in self._dir.iterdir()
            if d.is_dir() and not d.name.startswith("_") and (d / "mod.py").exists()
        ])

    def get(self, name: str) -> Dict[str, Any]:
        """Get an agent config by name"""
        if name in self._cache:
            return self._cache[name]
        mod_path = self._dir / name / "mod.py"
        if not mod_path.exists():
            raise KeyError(f"agent not found: {name}")
        spec = importlib.util.spec_from_file_location(f"agents.{name}", str(mod_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls = getattr(mod, "Agent", None)
        if cls is None:
            raise AttributeError(f"no Agent class in {name}/mod.py")
        config = {
            "name": getattr(cls, "name", name),
            "description": getattr(cls, "description", ""),
            "goal": getattr(cls, "goal", None),
            "icon": getattr(cls, "icon", ">_"),
            "skills": getattr(cls, "skills", None),
            "model": getattr(cls, "model", None),
            "cls": cls,
        }
        self._cache[name] = config
        return config

    def create(self, name: str, description: str = "", goal: str = "",
               icon: str = ">_", skills: list = None, model: str = None,
               key: str = None) -> Dict[str, Any]:
        """Create a new agent locally in agents/ directory.

        Args:
            name: agent slug (lowercase, no spaces)
            description: what this agent does
            goal: system prompt / goal for the agent
            icon: display icon
            skills: optional list of skill names to restrict to
            model: optional model override
            key: caller auth token (for permission check)
        """
        name = name.lower().replace(" ", "-").replace("_", "-")
        agent_dir = self._dir / name
        if agent_dir.exists():
            raise FileExistsError(f"agent already exists: {name}")

        agent_dir.mkdir(parents=True)
        label = name.replace("-", " ").title()
        content = AGENT_TEMPLATE.format(
            name=name,
            label=label,
            description=description or f"{label} agent",
            icon=icon,
            skills=repr(skills) if skills else "None",
            model=repr(model) if model else "None",
            goal=goal or f"You are a {label} agent.",
        )
        (agent_dir / "mod.py").write_text(content)

        # clear cache so new agent is discovered
        self._cache.pop(name, None)
        return self.get(name)

    def remove(self, name: str, key: str = None) -> Dict[str, Any]:
        """Remove a local agent."""
        agent_dir = self._dir / name
        if not agent_dir.exists():
            raise KeyError(f"agent not found: {name}")
        # don't allow removing built-in agents
        builtins = {"default", "architect", "reviewer", "debugger", "builder", "refactorer"}
        if name in builtins:
            raise PermissionError(f"cannot remove built-in agent: {name}")
        import shutil
        shutil.rmtree(agent_dir)
        self._cache.pop(name, None)
        return {"removed": name}

    # ── CID storage (save/load as content-addressed JSON) ──────────

    def _build_config(self, name: str = None, description: str = None,
                      goal: str = None, icon: str = ">_", skills: list = None,
                      model: str = None, memory: str = None) -> Dict[str, Any]:
        """Build an agent config dict from name/overrides."""
        config = {}
        if name and name in self.ls():
            existing = self.get(name)
            config = {k: v for k, v in existing.items() if k != "cls"}

        if description is not None:
            config["description"] = description
        if goal is not None:
            config["goal"] = goal
        if skills is not None:
            config["skills"] = skills
        if model is not None:
            config["model"] = model
        if icon != ">_" or "icon" not in config:
            config["icon"] = icon
        if memory is not None:
            config["memory"] = memory
        if name:
            config["name"] = name

        config["type"] = "agent"
        config["created"] = time.time()
        return config

    def save(self, name: str = None, description: str = None, goal: str = None,
             icon: str = ">_", skills: list = None, model: str = None,
             memory: str = None, private: bool = False,
             provider_key: str = None, client_key: str = None,
             key: str = None) -> Dict[str, Any]:
        """Save an agent definition as a JSON CID on localfs.

        Can save an existing agent by name, or create a new definition inline.
        With private=True, encrypts the agent with a 2-of-2 secret share
        between provider and client — both shares needed to decrypt.

        Args:
            name: existing agent name to snapshot, or new agent name
            description: agent description (override or new)
            goal: system prompt / goal (override or new)
            icon: display icon
            skills: list of skill names to bundle
            model: model override
            memory: memory module path (e.g. 'agent.memory')
            private: if True, encrypt with 2-of-2 secret sharing
            provider_key: provider's key name (for share delivery)
            client_key: client's key name (for share delivery)
            key: caller auth key

        Returns:
            Public:  {"cid": "Qm...", "name": ..., "agent": {...}}
            Private: {"cid": "Qm...", "name": ..., "private": True,
                      "provider_share": "...", "client_share": "...",
                      "provider_address": "...", "client_address": "..."}
        """
        if not m:
            raise RuntimeError("mod framework required for CID storage")

        config = self._build_config(name, description, goal, icon,
                                    skills, model, memory)

        if private:
            return self._save_private(config, provider_key, client_key)

        # public save — store plaintext
        localfs = m.mod("localfs")()
        cid = localfs.put(config)
        self._index_cid(cid, config.get("name", ""))
        return {"cid": cid, "name": config.get("name", ""), "agent": config}

    def _save_private(self, config: Dict[str, Any],
                      provider_key: str = None,
                      client_key: str = None) -> Dict[str, Any]:
        """Encrypt agent config with 2-of-2 Shamir secret sharing.

        1. Generate random 32-byte hex password
        2. AES-encrypt the agent JSON with that password
        3. Split password into 2-of-2 Shamir shares
        4. Store encrypted envelope as CID
        5. Return each party's share (never stored together)
        """
        # resolve key addresses for metadata
        provider = m.key(provider_key) if provider_key else m.key()
        client = m.key(client_key) if client_key else None
        if client is None:
            raise ValueError("client_key required for private agents")

        # generate ephemeral encryption password
        # secretshare field is 2^127-1, max 16 UTF-8 chars
        # 8 random bytes -> 16 hex chars = 64 bits entropy (SHA256-hashed for AES key)
        password = secrets.token_hex(8)

        # encrypt the full agent config
        plaintext = json.dumps(config)
        encrypted = m.encrypt(plaintext, password=password)

        # 2-of-2 Shamir split on the password
        ss = m.mod("secretshare")()
        shares = ss.secretshare(password, n=2, m=2)

        # build envelope — contains encrypted blob + metadata, no shares
        envelope = {
            "type": "agent",
            "private": True,
            "name": config.get("name", ""),
            "encrypted": encrypted if isinstance(encrypted, str) else encrypted.decode(),
            "provider": provider.address,
            "client": client.address,
            "created": config.get("created", time.time()),
        }

        # store envelope as CID
        localfs = m.mod("localfs")()
        cid = localfs.put(envelope)
        self._index_cid(cid, config.get("name", ""), private=True)

        return {
            "cid": cid,
            "name": config.get("name", ""),
            "private": True,
            "provider_share": shares[0],
            "client_share": shares[1],
            "provider_address": provider.address,
            "client_address": client.address,
        }

    def load(self, cid: str, shares: List[str] = None) -> Dict[str, Any]:
        """Load an agent definition from a JSON CID.

        For public agents, returns the config directly.
        For private agents, requires both 2-of-2 shares to decrypt.

        Args:
            cid: localfs CID string (e.g. 'localfs/QmXx...' or 'QmXx...')
            shares: list of 2 Shamir shares (required for private agents)

        Returns:
            Agent config dict with name, goal, skills, model, memory, etc.
        """
        if not m:
            raise RuntimeError("mod framework required for CID storage")

        localfs = m.mod("localfs")()
        data = localfs.get(cid)
        if not isinstance(data, dict):
            raise ValueError(f"CID does not contain a valid agent definition: {cid}")
        if data.get("type") != "agent":
            raise ValueError(f"CID content is not an agent (type={data.get('type')}): {cid}")

        # private agent — decrypt with shares
        if data.get("private"):
            if not shares or len(shares) < 2:
                raise PermissionError(
                    "Private agent requires 2-of-2 shares to decrypt. "
                    f"Provider: {data.get('provider')}, Client: {data.get('client')}"
                )
            ss = m.mod("secretshare")()
            password = ss.reconstruct(shares)
            decrypted = m.decrypt(data["encrypted"], password=password)
            if isinstance(decrypted, str):
                decrypted = json.loads(decrypted)
            return decrypted

        return data

    def load_and_create(self, cid: str, shares: List[str] = None,
                        key: str = None) -> Dict[str, Any]:
        """Load an agent from CID and install it locally.

        Args:
            cid: localfs CID
            shares: 2-of-2 Shamir shares (required for private agents)
            key: caller auth key

        Returns:
            Installed agent config
        """
        data = self.load(cid, shares=shares)
        name = data.get("name", "")
        if not name:
            raise ValueError("Agent CID has no name field")
        return self.create(
            name=name,
            description=data.get("description", ""),
            goal=data.get("goal", ""),
            icon=data.get("icon", ">_"),
            skills=data.get("skills"),
            model=data.get("model"),
            key=key,
        )

    def ls_cids(self) -> List[Dict[str, Any]]:
        """List all saved agent CIDs from the local index."""
        index_path = self._dir / ".agent_cids.json"
        if index_path.exists():
            with open(index_path) as f:
                return json.load(f)
        return []

    def _index_cid(self, cid: str, name: str, private: bool = False):
        """Append a CID to the local agent index."""
        index_path = self._dir / ".agent_cids.json"
        index = self.ls_cids()
        entry = {"cid": cid, "name": name, "saved": time.time()}
        if private:
            entry["private"] = True
        index.append(entry)
        with open(index_path, "w") as f:
            json.dump(index, f, indent=2)

    def register(self, name: str, key: str = None,
                 backend: str = "offchain") -> Dict[str, Any]:
        """Register an agent on the registry (local offchain or on-chain EVM).

        Args:
            name: agent name (must exist locally)
            key: auth key/token for ownership
            backend: 'offchain' (default) or 'evm' for on-chain
        """
        if not m:
            raise RuntimeError("mod framework required for registry")

        config = self.get(name)
        data = {k: v for k, v in config.items() if k != "cls"}
        data["type"] = "agent"

        registry = m.mod("registry")()
        result = registry.register(
            name=f"agent.{name}",
            data=data,
            backend=backend,
        )
        return {"agent": name, "registry": result}

    def schema(self) -> Dict[str, Dict]:
        """Get schemas for all agents"""
        out = {}
        for name in self.ls():
            try:
                config = self.get(name)
                out[name] = {k: v for k, v in config.items() if k != "cls"}
            except Exception as e:
                out[name] = {"error": str(e)}
        return out

    def forward(self, name: str = None, **kwargs) -> Any:
        """Mod protocol entry point.

        forward()                           -> list all agents
        forward("architect")                -> get agent config
        forward(action="create", name=...)  -> create new agent
        forward(action="remove", name=...) -> remove agent
        forward(action="save", name=...)   -> save agent as JSON CID
        forward(action="load", cid=...)    -> load agent from CID
        forward(action="install", cid=...) -> load from CID and install locally
        forward(action="cids")             -> list saved agent CIDs
        forward(action="register", name=...)-> register on-chain
        """
        action = kwargs.get("action")

        if action == "create":
            return self.create(
                name=kwargs.get("name", name or ""),
                description=kwargs.get("description", ""),
                goal=kwargs.get("goal", ""),
                icon=kwargs.get("icon", ">_"),
                skills=kwargs.get("skills"),
                model=kwargs.get("model"),
                key=kwargs.get("key"),
            )
        if action == "remove":
            return self.remove(name=kwargs.get("name", name or ""), key=kwargs.get("key"))
        if action == "save":
            return self.save(
                name=kwargs.get("name", name or ""),
                description=kwargs.get("description"),
                goal=kwargs.get("goal"),
                icon=kwargs.get("icon", ">_"),
                skills=kwargs.get("skills"),
                model=kwargs.get("model"),
                memory=kwargs.get("memory"),
                private=kwargs.get("private", False),
                provider_key=kwargs.get("provider_key"),
                client_key=kwargs.get("client_key"),
                key=kwargs.get("key"),
            )
        if action == "load":
            return self.load(
                cid=kwargs.get("cid", name or ""),
                shares=kwargs.get("shares"),
            )
        if action == "install":
            return self.load_and_create(
                cid=kwargs.get("cid", name or ""),
                shares=kwargs.get("shares"),
                key=kwargs.get("key"),
            )
        if action == "cids":
            return {"cids": self.ls_cids()}
        if action == "register":
            return self.register(
                name=kwargs.get("name", name or ""),
                key=kwargs.get("key"),
                backend=kwargs.get("backend", "offchain"),
            )

        if name is None:
            return {"agents": self.ls(), "total": len(self.ls()), "schemas": self.schema()}
        return {k: v for k, v in self.get(name).items() if k != "cls"}

    def chains(self) -> Dict[str, Any]:
        """Get chain presets from chains.json if it exists"""
        chains_path = self._dir / "chains.json"
        if chains_path.exists():
            with open(chains_path) as f:
                return json.load(f)
        return {}
