"""chain-mod agent - hands the run to the chain console's Claude Code harness"""


class Agent:
    name = "Chain Console"
    description = ("Claude Code over a chain-console project — it reads and edits the "
                   "builder's contracts and tests, runs `npx hardhat test` in a sandbox, "
                   "and its edits land back in the project")
    icon = "⛓"
    tools = None
    model = None
    # same shape as build-mod, a different workspace: the run happens in a
    # Hardhat project the chain module lays out from the caller's saved
    # project (contracts/ + test/), with edits accepted there and nowhere
    # else, and the shell limited to hardhat. Pass harness_args
    # {project, address, network} to pick the project; unnamed lands on
    # the caller's project called "agent".
    harness = "chainmod"
    owner = None

    goal = """You are the chain console's agent. The builder handed you their Solidity
project: read the contracts and tests before you change anything, keep changes
minimal, prove them with `npx hardhat test`, and never try to deploy — deploys
are signed by the builder's wallet in the console. End with a short plain
answer: what you changed, what the tests say. That final message is all they see."""
