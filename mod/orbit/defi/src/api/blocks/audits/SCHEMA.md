# Block audits

One JSON file per catalog block, `<block id>.json`, written by an agent that read
`common.sol`, the block's `.sol`, and its catalog entry. Served at
`GET /catalog/{id}/audit`, summarised on every block in `GET /catalog`, and
exposed as the `defi_audit` MCP tool.

```json
{
  "block": "vault",
  "contract": "ModVault",
  "file": "vault.sol",
  "audited_at": "2026-08-30",
  "auditor": "claude-fable-5 agent audit",
  "risk": "low | medium | high | critical",
  "summary": "one paragraph: what the contract does, the worst thing found, and whether it is safe to deploy as-is",
  "counts": { "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0 },
  "findings": [
    {
      "id": "VAULT-1",
      "severity": "critical | high | medium | low | info",
      "title": "short claim",
      "where": "function name and line range, e.g. deposit() L41-L55",
      "detail": "what is wrong and why it matters",
      "exploit": "concrete sequence of calls / state that triggers it",
      "recommendation": "the fix"
    }
  ],
  "safe_use": ["deployment guidance a user should follow if they use this block as-is"]
}
```

`risk` is the auditor's overall verdict, not a formula over counts. These are
agent audits of unaudited reference implementations — they reduce the unknowns,
they do not certify anything.
