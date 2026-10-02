"""make_agent - create a new agent from a plain-language brief, mid-run"""
import concurrent.futures
from typing import Any, Dict


class Tool:
    description = ("Create a new agent for the user from a plain-language brief — "
                   "what it should do, in a sentence or two. The vibe-builder "
                   "designs the whole thing (name, icon, system prompt, tools from "
                   "the live catalog) and files it under the user's address. Use "
                   "it when the user asks you to make, build or set up an agent; "
                   "never ask them to fill in fields.")
    needs_context = True
    context = None

    # `brief` and `slug`, not `description`/`name`: the registry dispatches on
    # run(name, **params), so a param called `name` collides with the tool's
    def forward(self, brief: str, slug: str = None, **kwargs) -> Dict[str, Any]:
        """
        Design and save one agent.

        Args:
            brief: what the agent is for and how it should work, in plain words
            slug: optional name for it (lowercase-dashes); leave it out and an
                  untaken one is made up
        """
        box = self.context
        if box is None or not hasattr(box, 'agent_vibe'):
            return {"success": False, "error": "this agent can't create agents here"}
        key = getattr(box, '_run_key', None)
        if not key:
            return {"success": False,
                    "error": "creating an agent needs a signed-in caller — ask the "
                             "user to sign in, or use the agents rail"}
        brief = str(brief or '').strip()
        if len(brief) < 8:
            return {"success": False, "error": "describe the agent in a sentence or two"}
        # the drafting run is a run of its own: on this thread it would take
        # over — and on exit clear — the step callback, sandbox and key of the
        # run that called this tool. A fresh thread is a fresh threading.local.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(box.agent_vibe, description=brief,
                              name=(str(slug).strip() or None) if slug else None,
                              save=True, key=key, direct=True)
            try:
                out = fut.result(timeout=300)
            except concurrent.futures.TimeoutError:
                return {"success": False, "error": "drafting the agent timed out"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        if out.get('error'):
            return {"success": False, "error": out['error']}
        d = out.get('draft') or {}
        return {"success": True, "agent": d.get('name'), "icon": d.get('icon'),
                "description": d.get('description'), "tools": d.get('tools') or 'all',
                **({"tools_dropped": out['tools_dropped']} if out.get('tools_dropped') else {}),
                "note": f"created '{d.get('name')}' — it is in the agents rail, ready to run"}

    def test(self):
        assert Tool().forward(brief="an agent that reviews diffs")["success"] is False

        class Box:
            _run_key = None
            calls = []

            def agent_vibe(self, **kw):
                Box.calls.append(kw)
                return {"draft": {"name": "diff-hawk", "icon": "◆",
                                  "description": "reviews diffs", "tools": ["read"]},
                        "saved": True}
        t = Tool()
        t.context = Box()
        assert t.forward(brief="an agent that reviews diffs")["success"] is False
        Box._run_key = "tok"
        assert t.forward(brief="short")["success"] is False
        r = t.forward(brief="an agent that reviews diffs")
        assert r["success"] and r["agent"] == "diff-hawk"
        assert Box.calls[-1]["save"] is True and Box.calls[-1]["key"] == "tok"
        return True
