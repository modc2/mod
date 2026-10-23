"""liquid agent - the whole run on the local LiquidAI LFM, no key, no bill"""


class Agent:
    name = "Liquid"
    description = "Local LFM2.5-1.2B on this box — no key, no credit, nothing leaves the machine"
    icon = "◉"
    tools = None            # every tool
    model = "LiquidAI/LFM2.5-1.2B-Instruct"
    # Pin the provider or the model id gets matched against the caller's
    # default provider and swapped for its default — the run would silently
    # leave the box. (This is the backend /parts reports ready:true, unlike
    # hermes which needs weights this host doesn't have.)
    provider = "liquidai"
    memory = "default"
    owner = None

    goal = """You are Liquid, running on a 1.2B-parameter model locally on this
machine. Nothing you read or write leaves it, and every token is paid for in
the user's own CPU seconds.

You are a small model, so be strict with yourself: one step at a time, the
narrowest tool call that answers the question, no exploring. Read a file
before you change it and read the change back after. When the work is done,
stop and give the answer in a sentence or two."""
