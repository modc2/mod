"""remember - store a durable fact in the agent's memory module"""
from typing import Any, Dict, List


class Tool:
    description = ("Store a fact worth keeping across runs — a convention, a "
                   "decision, where something lives. Future runs retrieve it "
                   "with recall. Don't store what the code already says.")
    needs_context = True
    context = None

    # `fact`, not `name`: the registry dispatches on run(name, **params), so a
    # tool whose own param is `name` collides with the name of the tool itself
    def forward(self, fact: str, content: str, tags: List[str] = None,
                **kwargs) -> Dict[str, Any]:
        """
        Write one fact into the semantic layer, keyed by its name.

        Storing the same name again replaces it, so a fact that changed is
        corrected rather than duplicated.

        Args:
            fact: short key for the fact ('test command', 'api port')
            content: the fact itself, in one or two sentences
            tags: optional labels to group facts by
        """
        mem = getattr(self.context, 'memory', None)
        if mem is None:
            return {"success": False, "error": "no memory module attached to this agent"}
        if not str(fact or '').strip() or not str(content or '').strip():
            return {"success": False, "error": "a fact needs both a name and content"}
        try:
            stored = mem.remember(str(fact).strip(), str(content).strip(),
                                  tags=list(tags) if tags else None)
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": True, "fact": stored,
                "note": f"stored as '{stored.get('id', fact)}' — future runs can recall it"}

    def test(self):
        assert Tool().forward(fact="k", content="v")["success"] is False

        class Box:
            class memory:
                @staticmethod
                def remember(name, content, tags=None):
                    return {'id': name, 'name': name, 'content': content,
                            'tags': tags or []}
        t = Tool()
        t.context = Box()
        r = t.forward(fact="port", content="the api listens on 50117", tags=["net"])
        assert r["success"] and r["fact"]["content"].endswith("50117")
        assert t.forward(fact="", content="no name")["success"] is False
        return True
