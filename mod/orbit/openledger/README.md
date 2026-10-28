# openledger

Key custody without buying a hardware wallet. Shard a key (seed phrase, private
key, key file) into **N pieces**, write **one piece per USB stick**, and store the
sticks apart. Any **K** of them plugged back into one machine rebuild the key;
fewer than K reveal *nothing* — lose a stick, or have one stolen, and the thief
holds random bytes. This is Shamir's Secret Sharing over GF(256).

- **No single point of failure, no vendor:** the whole key never touches any one
  disk — `shard` splits in memory and each stick only ever sees its own piece.
  Dead stick? Any K of the survivors still restore. No seed card in a drawer,
  no $150 dongle, no supply chain to trust.
- **A ledger, not a vault:** `~/.openledger/ledger.json` records which drive
  (serial/UUID) got which share of which set — never the key, never a share.
  Safe to lose, safe to back up.
- **One format everywhere:** shares are `ss1.<set>.<k>.<x>.<payload>`, byte-compatible
  with orbit/secretshare (`shamir/` here is a vendored copy of its engine), so a
  piece written by one module combines in the other. A 4-byte checksum is split
  along with the key, so corrupt, tampered or mixed-up pieces fail loudly
  instead of returning garbage.

```
m openledger/usbs                                  # which USB drives are plugged in
m openledger/shard path=./key.txt n=3 k=2 label=main   # one share per stick; key never printed
m openledger/check                                 # which sets could be rebuilt right now
m openledger/restore set=ab12cd34 out=./key.txt    # or omit out= to print
m openledger/sets                                  # the ledger
m openledger/wipe set=ab12cd34                     # retire a set from plugged-in drives
m openledger/test                                  # end-to-end self-check (temp dirs as sticks)
```

No USB sticks handy (or testing on a server)? Every drive-touching fn takes
`drives=/path/a,/path/b` to use plain directories instead.

Plain string ops (`split` / `combine` / `inspect`) are also exposed for
secretshare parity when you want shares without the USB workflow.

**Honest caveats:** restoring necessarily reassembles the key in RAM on the
machine you run it on — do that on a box you trust, ideally offline. `wipe`
overwrites before deleting, but flash wear-leveling means the only sure erase of
a retired stick is physical destruction. Pick K ≥ 2 and store sticks in
different places; K sticks in one drawer is the drawer's security.

Layout: `shamir/` engine (vendored from secretshare) · `mod.py` CLI · no server, no network — everything is local.
