"""inkling agent - free-tier coding over OpenRouter's $0 shelf"""


class Agent:
    name = "Inkling"
    description = "Free-tier coder — OpenRouter's $0 Inkling Small, tools and a 1M context, zero spend"
    icon = "◌"
    tools = None            # every tool
    # cheapy's cheapest tool-capable pick (2026-10-23): $0.00/M in and out,
    # 1M context, tool calling. Free-tier models get rate-limited and
    # re-shelved upstream — if runs start failing, ask cheapy again.
    model = "thinkingmachines/inkling-small:free"
    # Without this the model id is read against the caller's default provider,
    # recognised as belonging elsewhere, and quietly swapped for that
    # provider's default — the run would stop being free.
    provider = "openrouter"
    memory = "default"
    owner = None

    goal = """You are Inkling, a coding agent on a free-tier model. The model costs
nothing, but free tiers are rate-limited and patience is not: keep every step
small and deliberate. Read a file before you change it, prefer a narrow tool
call over a broad one, and verify your edit by reading it back. If a request
is refused or rate-limited, wait out one retry rather than hammering. End
with a short answer to the user — that final message is all they see."""
