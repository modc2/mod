# CoPlay

Organize pickup games at any location. Charge crypto entry fees via smart contract.

## How It Works

1. **Organizer** creates a game (title, location, date, entry fee)
2. **Players** join by paying the entry fee in ETH through the CoPlayHub contract
3. **Organizer** completes the game — funds are released minus the admin fee
4. **Admin** earns a configurable fee (0-10%) on every completed game

## Smart Contract: CoPlayHub

- Entry fees held in escrow until game completes
- Admin fee: 0-1000 basis points (0%-10%)
- Admin can require game approval before going live
- Players can claim refunds if a game is cancelled

## Usage

```python
import mod as m

coplay = m.mod('coplay')()

# List games
games = coplay.games()

# Create game (requires auth)
game = coplay.create_game(
    token=token,
    title="5v5 Basketball",
    game_type="basketball",
    location="Central Park",
    date="2026-04-20",
    time_str="18:00",
    max_players=10,
    entry_fee="0.005",
)
```

```bash
# CLI
m coplay games
m coplay/create_game title="5v5 Basketball" game_type=basketball location="Central Park"
```

## Structure

```
coplay/
├── coplay/
│   └── mod.py           # Off-chain metadata API
├── contracts/
│   └── CoPlayHub.sol    # On-chain entry fees + admin fee
└── README.md
```
