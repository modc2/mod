"""
openhouse bank — connect any bank, turn its transfers into rent.

    from bank import open_bank
    b = open_bank(data_dir, secrets_dir)
    b.connect('sandbox')                      # testnet bank, local JSON
    b.link('0xRenter…')                       # → reference code OH-7F3A2C
    b.adapter().receive(amount=1500, reference='OH-7F3A2C rent')
    b.reconcile(record=…, to_units=…)         # books it exactly once

Adapters (one file each, one shape out — see core.py):
    sandbox      local fake bank for testnet            sandbox.py
    statement    camt.053 / MT940 / OFX / CSV in,
                 ISO 20022 pain.001 out — any bank      statements.py
    openbanking  Berlin Group PSD2 / UK OBIE APIs       openbanking.py
    mcp          a bank that runs its own MCP server    remote.py

Adding a bank = one Adapter subclass + one line in ADAPTERS.

No openhouse imports anywhere in this package: copy the directory into any
module that needs "did the money arrive, and whose was it".
"""
from .core import Adapter, Bank, BankError, reference_code  # noqa: F401
from .openbanking import OpenBanking
from .remote import RemoteMCP
from .sandbox import Sandbox
from .statements import Statements, parse as parse_statement, pain001  # noqa: F401

ADAPTERS = {cls.kind: cls for cls in (Sandbox, Statements, OpenBanking, RemoteMCP)}


def open_bank(data_dir, secrets_dir) -> Bank:
    return Bank(data_dir, secrets_dir, ADAPTERS)
