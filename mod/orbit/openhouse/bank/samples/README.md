# Sample bank statements (fake, testnet)

One month of the demo landlord's rent account in each of the four formats the
`statement` adapter reads. All names, IBANs and amounts are fictional; the
`OH-9B3CD4` / `OH-E0F923` memos are the reference codes of the demo renters
Maya and Jonah (`demo.py`), so importing any of these and running
`bank_reconcile` books their rent onto the testnet ledger.

| file | format | typical source |
|---|---|---|
| `camt053.xml` | ISO 20022 camt.053.001.02 | EU / UK / CH / AU online banking "XML statement" |
| `mt940.txt` | SWIFT MT940 | legacy corporate export, most banks worldwide |
| `statement.ofx` | OFX 1.x SGML | US / Canada "Quicken / Money download" |
| `statement.csv` | CSV | any bank's "export to spreadsheet" |
