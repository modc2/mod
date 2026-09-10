"""toolbox - list the tool bundles, and snap one on mid-run"""
from typing import Any, Dict


class Tool:
    description = ("List the tool bundles available to you, or snap one on to gain "
                   "its tools for the rest of this run. Call it with no arguments "
                   "to see what exists, then with a name when you need those tools.")
    needs_context = True
    context = None

    # the parameter is `box`, not `name`: the registry dispatches on
    # run(name, **params), so a tool whose own param is `name` collides with
    # the name of the tool being run
    def forward(self, box: str = None, **kwargs) -> Dict[str, Any]:
        """
        Inspect or extend your own loadout.

        Args:
            box: the bundle to snap on (e.g. 'vcs', 'verify'). Leave it out
                 to list every bundle with the tools it carries.
        """
        boxes = getattr(self.context, 'toolboxes', None)
        if boxes is None:
            return {"success": False, "error": "no toolbox registry attached to this agent"}
        if not box:
            return {
                "success": True,
                "toolboxes": [{"name": b["name"], "description": b["description"],
                               "tools": b["tools"]} for b in boxes.items()],
                "note": "call toolbox again with one of these names to snap it on",
            }
        try:
            return {"success": True, **self.context.use_toolbox(str(box).strip())}
        except KeyError:
            return {"success": False,
                    "error": f"no such toolbox: {box}. available: {boxes.ls()}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def test(self):
        assert Tool().forward()["success"] is False

        class Boxes:
            @staticmethod
            def items():
                return [{"name": "vcs", "description": "version control",
                         "tools": ["git", "diff"]}]

            @staticmethod
            def ls():
                return ["vcs"]

        class Box:
            toolboxes = Boxes()

            @staticmethod
            def use_toolbox(n):
                if n != "vcs":
                    raise KeyError(n)
                return {"toolbox": n, "added": ["git"], "tools": ["read", "git"]}

        t = Tool()
        t.context = Box()
        listed = t.forward()
        assert listed["success"] and listed["toolboxes"][0]["name"] == "vcs"
        snapped = t.forward(box="vcs")
        assert snapped["success"] and snapped["added"] == ["git"]
        assert t.forward(box="nope")["success"] is False
        return True
