"""recall - retrieval over the agent's own memory module"""
from typing import Any, Dict, List, Optional

LAYERS = ('semantic', 'dialogue', 'episodic', 'working')


class Tool:
    description = ("Search your own memory: durable facts, past conversations with "
                   "this user, and steps you already took. Use it before repeating "
                   "work or when the user refers to something from before.")
    # handed the live agent so this searches the memory the run is using,
    # not a fresh empty one (see tools/mod.py bind)
    needs_context = True
    context = None

    def memory(self):
        mem = getattr(self.context, 'memory', None)
        if mem is None:
            raise RuntimeError("no memory module attached to this agent")
        return mem

    def forward(self, query: str, k: int = 5, layer: str = None,
                **kwargs) -> Dict[str, Any]:
        """
        Retrieve what memory holds about a query, ranked best first.

        Args:
            query: what you are trying to remember, in words
            k: how many hits per layer (default 5)
            layer: restrict to one layer — 'semantic' (facts you were told or
                   stored), 'dialogue' (past turns with this user), 'episodic'
                   (steps you ran), 'working' (this run's own state). Default
                   searches facts, dialogue and steps together.
        """
        try:
            mem = self.memory()
        except Exception as e:
            return {"success": False, "error": str(e)}
        if layer and layer not in LAYERS:
            return {"success": False,
                    "error": f"unknown layer: {layer}. use one of {list(LAYERS)}"}
        try:
            hits = mem.retrieve(query, k=int(k), layers=[layer] if layer else None,
                                session=getattr(self.context, '_session', None),
                                who=getattr(self.context, '_who', None))
        except AttributeError:
            # a memory module without retrieval still has the semantic layer
            hits = [{'layer': 'semantic', 'id': f.get('id'), 'name': f.get('name'),
                     'text': f.get('content', ''), 'score': f.get('score')}
                    for f in mem.recall(query, k=int(k))]
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {
            "success": True,
            "query": query,
            "hits": hits,
            "total": len(hits),
            "note": ("nothing in memory matches — this is new to you"
                     if not hits else
                     "recalled from your own memory; treat facts as true and "
                     "past turns as things already said"),
        }

    def test(self):
        # unbound: reports the missing box rather than inventing a memory
        r = Tool().forward("anything")
        assert r["success"] is False

        class Box:
            class memory:
                @staticmethod
                def retrieve(q, k=5, layers=None, session=None, who=None):
                    return [{'layer': 'semantic', 'id': 'x', 'name': 'x',
                             'text': q, 'score': 1.0, 'ts': None}]
        t = Tool()
        t.context = Box()
        r = t.forward("port")
        assert r["success"] and r["total"] == 1 and r["hits"][0]["text"] == "port"
        assert t.forward("port", layer="nope")["success"] is False
        return True
