"""calc — a bot that answers every drill in this pack, correctly.

Every drill needs a ceiling. Without one, a leaderboard of models says which
model is least bad and nothing about whether the questions were answerable at
all; with one, a model's score reads against a seat that is known to be right,
and a drill nobody can beat is visibly a broken drill rather than a hard one.

So this plays addup, times, jsonpath, jsonfix and invoice, off the same view a
model gets and nothing else. It shares one thing with every seat at these
tables: the view is all it has. It parses the drill's name out of the first
line, the question out of the `Question:` line, and the document — where there
is one — out of the braces, exactly as an answerer would have to.

The jsonfix half is worth being plain about: it repairs the six faults that
drill shows, in the order that makes them compose. A general JSON repairer is a
different and much larger program, and claiming this is one would be a lie the
leaderboard would eventually catch.

    m arena/upload path=bot_calc.py
    m arena/enter name=calc kind=class config='{"module":"bot_calc"}'
"""

import json
import re


class Calc:
    """Reads the view, does the work, answers in the format the view asked for."""

    name = "calc"

    def play(self, view, seat):
        drill = self.drill(view)
        question = self.question(view)
        if drill in ("addup", "times"):
            return str(self.arithmetic(question))
        if drill == "jsonpath":
            return self.value_at(view, question)
        if drill == "jsonfix":
            return self.fixed(view)
        if drill == "invoice":
            return str(self.invoice(view, question))
        # Not one of ours. Say nothing rather than guess — an illegal move is
        # honest about not having played, and a made-up one is not.
        return ""

    # ── reading the view ─────────────────────────────────────────────────

    def drill(self, view):
        head = view.strip().splitlines()[0] if view.strip() else ""
        parts = head.split()
        return parts[1] if len(parts) > 1 and parts[0] == "DRILL" else ""

    def question(self, view):
        for line in view.splitlines():
            if line.startswith("Question:"):
                return line.split(":", 1)[1].strip()
        return ""

    def document(self, view):
        """The JSON in the view, parsed. The drills put exactly one there."""
        span = self.braces(view)
        try:
            return json.loads(span) if span else None
        except ValueError:
            return None

    def braces(self, text):
        """The first brace-balanced span, or "" if the braces never close."""
        start = text.find("{")
        if start < 0:
            return ""
        depth, in_string, escaped = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == '"':
                    in_string = False
                continue
            if c == '"':
                in_string = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        return ""

    # ── addup, times ─────────────────────────────────────────────────────

    def arithmetic(self, text):
        """Evaluate `12 + 45 - 7` or `481 * 27 * 3`, exactly, in integers.

        Written out rather than handed to `eval`, which the sandbox is right to
        be nervous about and which would evaluate rather more than arithmetic.
        """
        total, running, sign, multiply = 0, None, 1, False
        for token in re.findall(r"[-+*]|\d+", text):
            if token.isdigit():
                n = int(token)
                running = running * n if (multiply and running is not None) else n
                multiply = False
            elif token == "*":
                multiply = True
            else:
                if running is not None:
                    total += sign * running
                    running = None
                sign = 1 if token == "+" else -1
        return total + (sign * running if running is not None else 0)

    # ── jsonpath ─────────────────────────────────────────────────────────

    def value_at(self, view, question):
        doc = self.document(view)
        path = question.replace("value at", "").strip()
        node = doc
        try:
            for part in path.split("."):
                node = node[int(part)] if part.isdigit() else node[part]
        except (KeyError, IndexError, TypeError, ValueError):
            return ""
        if isinstance(node, bool):
            return "true" if node else "false"
        return str(node)

    # ── invoice ──────────────────────────────────────────────────────────

    def invoice(self, view, question):
        doc = self.document(view) or {}
        lines = doc.get("lines", [])
        subtotal = sum(l["qty"] * l["unit"] for l in lines)
        discounted = subtotal - doc.get("discount", 0)
        tax = discounted * doc.get("tax_percent", 0) // 100

        # Most specific first: "grand total" and "subtotal after the discount"
        # both contain the word every looser test would match on.
        if "grand total" in question:
            return discounted + tax
        if question.startswith("tax on"):
            return tax
        if "after the discount" in question:
            return discounted
        if question.startswith("subtotal"):
            return subtotal
        if "units of" in question:
            sku = question.split("units of", 1)[1].split()[0].strip(" .,")
        else:
            sku = question.replace("line total for", "").split("—")[0].strip()
        line = next((l for l in lines if l["sku"] == sku), None)
        if line is None:
            return 0
        return line["qty"] if "units of" in question else line["qty"] * line["unit"]

    # ── jsonfix ──────────────────────────────────────────────────────────

    def fixed(self, view):
        """Repair the literal in the view and hand back canonical JSON.

        The six faults compose in this order and only in this order: strip the
        comments before anything reads the text as JSON, swap the quote style
        before quoting keys (or the new quotes get quoted), quote the keys
        before dropping trailing commas (both walk the same commas), and close
        the braces last, once the text is otherwise well formed.
        """
        start = view.find("{")
        end = view.find("\nQuestion:")
        text = view[start:end if end > start else len(view)].strip()

        text = re.sub(r"//[^\n]*", "", text)
        if '"' not in text:
            text = text.replace("'", '"')
        for python, real in (("True", "true"), ("False", "false"), ("None", "null")):
            text = re.sub(rf"(?<![\w\"]){python}(?![\w\"])", real, text)
        text = "\n".join(self.quote_key(line) for line in text.splitlines())
        text = re.sub(r",(\s*[}\]])", r"\1", text)
        text += self.closers(text)

        try:
            return json.dumps(json.loads(text))
        except ValueError:
            # Still not JSON. Hand back what we have: a repair that failed is
            # worth more on the transcript than an empty move.
            return text

    def quote_key(self, line):
        m = re.match(r"^(\s*)([A-Za-z_][A-Za-z0-9_-]*)(\s*:\s*)(.*)$", line)
        return f'{m.group(1)}"{m.group(2)}"{m.group(3)}{m.group(4)}' if m else line

    def closers(self, text):
        """Whatever is still open, closed in the right order."""
        stack, in_string, escaped = [], False, False
        for c in text:
            if in_string:
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == '"':
                    in_string = False
                continue
            if c == '"':
                in_string = True
            elif c in "{[":
                stack.append("}" if c == "{" else "]")
            elif c in "}]" and stack:
                stack.pop()
        return "".join(reversed(stack))
