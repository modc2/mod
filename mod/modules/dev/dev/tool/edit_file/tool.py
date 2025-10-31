import mod as c
import os
import re
from typing import Optional

print = c.print

class Tool:
    def forward(
        self,
        path: str = "./",
        content: str = "hey",
        start_anchor: Optional[str] = None,
        end_anchor: Optional[str] = None,
        create_if_missing: bool = True,
        strict: bool = False,
        use_regex: bool = False,
        **kwargs
    ) -> str:
        """
        Smartly insert content into a file.

        Behavior:
        - If both start and end anchors exist → replace text between them
        - If only start anchor exists → insert after it
        - If only end anchor exists → insert before it
        - If neither exists → append to end
        """
        path = os.path.abspath(path)

        # --- load or create file ---
        if not os.path.exists(path):
            if create_if_missing:
                c.print(f"[+] Creating new file: {path}", color="yellow")
                c.write(path, "")
                text = ""
            else:
                raise FileNotFoundError(f"File not found: {path}")
        else:
            text = c.text(path)

        # --- helpers ---
        def find_anchor(pattern: str, s: str, start: int = 0) -> Optional[int]:
            if not pattern:
                return None
            if use_regex:
                m = re.search(pattern, s[start:])
                return m.start() + start if m else None
            pos = s.find(pattern, start)
            return pos if pos != -1 else None

        def find_anchor_end(pattern: str, s: str, start: int = 0) -> Optional[int]:
            if not pattern:
                return None
            if use_regex:
                m = re.search(pattern, s[start:])
                return m.end() + start if m else None
            pos = s.find(pattern, start)
            return pos + len(pattern) if pos != -1 else None

        result = text

        # --- intelligent insertion logic ---
        start_pos = find_anchor_end(start_anchor, text) if start_anchor else None
        end_pos = find_anchor(end_anchor, text, start_pos or 0) if end_anchor else None

        if start_anchor and end_anchor and start_pos is not None and end_pos is not None and end_pos > start_pos:
            # Replace between anchors
            result = text[:start_pos] + content + text[end_pos:]
            c.print(f"[=] Inserted between anchors.", color="cyan")

        elif start_pos is not None:
            # Only start anchor found → insert after
            result = text[:start_pos] + content + text[start_pos:]
            c.print(f"[=] Inserted after start anchor.", color="cyan")

        elif end_pos is not None:
            # Only end anchor found → insert before
            result = text[:end_pos - len(end_anchor)] + content + text[end_pos - len(end_anchor):]
            c.print(f"[=] Inserted before end anchor.", color="cyan")

        else:
            msg = f"No anchors found."
            if strict:
                raise ValueError(msg)
            c.print(f"[!] {msg} Appending to end.", color="yellow")
            result = text + content

        # --- write result if changed ---
        if result != text:
            c.write(path, result)
            c.print(f"[✓] Updated file: {path}", color="green")
        else:
            c.print(f"[=] No change to file: {path}", color="blue")

        return result
