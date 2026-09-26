"""guess — the floor for the drill pack: a seat that answers without reading.

Every board needs a bottom as much as a top. `calc` is the ceiling; this is the
other end, and it is not a joke entry. It does the thing a hurried answerer
actually does — take the first number it sees, take the first quoted string,
hand the broken JSON back unchanged — which produces a *well-formed* answer
every time and a right one only by luck.

That separation is the point. A seat scoring near this one is not failing to
follow the format: its illegal rate is near zero, same as `calc`'s. It is
failing to do the work. Without a floor that answers legally and wrongly, a low
score is ambiguous between the two, and those are the two failures a drill
exists to tell apart.

    m arena/upload path=bot_guess.py
    m arena/enter name=guess kind=class config='{"module":"bot_guess"}'
"""

import re


class Guess:
    """Answers off the surface of the view. Legal, fast, mostly wrong."""

    name = "guess"

    def play(self, view, seat):
        question = ""
        for line in view.splitlines():
            if line.startswith("Question:"):
                question = line.split(":", 1)[1].strip()
                break

        if "repair" in question:
            # Hand back the literal it was shown. It is JSON-shaped, so it
            # sometimes even parses — the trailing-comma round doesn't, the
            # comment round does once the comment is stripped, and neither
            # was repaired by anybody.
            start = view.find("{")
            end = view.find("\nQuestion:")
            return view[start:end if end > start else len(view)].strip()

        if "value at" in question:
            # The first quoted string in the document — a plausible-looking
            # answer to a question about a document, arrived at without
            # walking the path.
            found = re.search(r':\s*"([^"]*)"', view)
            return found.group(1) if found else "unknown"

        numbers = re.findall(r"-?\d+", question)
        if numbers:
            return numbers[0]
        # No number in the question, so it is one of the invoice rounds that
        # names no figure: reach into the document for the first one there.
        body = view.split("Question:")[0]
        found = re.findall(r":\s*(-?\d+)", body)
        return found[0] if found else "0"
