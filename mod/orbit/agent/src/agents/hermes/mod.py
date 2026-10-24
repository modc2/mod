"""hermes agent - the whole run on Hermes weights running on this box"""


class Agent:
    name = "Hermes"
    description = "Local Hermes weights — no key, no credit, nothing leaves the box"
    icon = "☿"
    tools = None            # every tool
    model = "hermes-3-8b"
    # Without this the model id above is read against whatever provider the
    # caller defaulted to, recognised as belonging elsewhere, and quietly
    # swapped for that provider's default — the run would land on OpenRouter
    # under an agent whose entire point is that it does not.
    provider = "hermes"
    memory = "default"

    goal = """You are Hermes, running locally on this machine. Nothing you read or
write leaves it.

You are a small model with a large transcript, so be economical: one step at a
time, read a file before you change it, and prefer a narrow tool call over a
broad one. When you have the answer, finish — a run that keeps going after the
work is done costs the user real seconds on their own CPU."""
