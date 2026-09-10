"""
Zcash, explained to someone who has never used it.

This is the module's teaching surface. It exists because every other function
here assumes you already know what a shielded pool is, what a viewing key
gives away, and why the same wallet has two addresses that look nothing alike.
Someone who does not know those things cannot use this module safely -- they
can lose money by pasting the wrong address into the right box.

The content is deliberately written, not generated. Each lesson is short, in
plain language, and ends with something you can actually run in this module,
because reading about a shielded address and looking at your own are different
kinds of understanding. `TRY` entries name a real function and real arguments;
the console renders them as buttons and the agent in agent.py quotes them.

Two rules the text follows everywhere:

  * No lie of omission about what this module cannot do. If a lesson describes
    shielded spending, it says in the same breath that this module cannot
    perform one without a node.
  * No security theatre. "Shielded is private" is not a lesson; *what leaks
    anyway* is the lesson.
"""

# ── Lessons ─────────────────────────────────────────────────────────────────
#
# level: "start" (read these first), "core", "deep" (only when you need it)

LESSONS = [
    {
        "id": "what-is-zcash",
        "title": "What Zcash actually is",
        "level": "start",
        "minutes": 2,
        "summary": "A Bitcoin-shaped currency with an optional invisibility "
                   "cloak. The cloak is the whole point, and it is optional.",
        "body": [
            "Zcash is digital money that works a lot like Bitcoin: there is a "
            "public ledger, blocks are mined every 75 seconds or so, and there "
            "will only ever be 21 million ZEC. If you have used Bitcoin, most "
            "of your instincts carry over.",

            "The difference is that Zcash has a second, private way to hold "
            "and move the same coins. On the public side, everyone can see "
            "every address and every amount forever -- that is how Bitcoin "
            "works too, and most people are surprised by it. On the private "
            "side, the ledger records that a valid payment happened without "
            "recording who paid, who was paid, or how much.",

            "That is not done by trusting anyone. It is done with a piece of "
            "mathematics called a zero-knowledge proof: the sender proves 'I "
            "own coins and I am not spending them twice' in a way anyone can "
            "check, while the coins themselves stay encrypted. Nobody -- not "
            "miners, not the Zcash company, not this module -- can decrypt "
            "them without a key you hold.",

            "So a single Zcash wallet lives in two worlds at once. Money on "
            "the public side is called TRANSPARENT. Money on the private side "
            "is called SHIELDED. Moving between them is normal and allowed, "
            "and the moment you move is itself public. Almost everything "
            "confusing about Zcash comes from this one split.",
        ],
        "terms": ["shielded", "transparent", "zero-knowledge proof", "ZEC"],
        "try": [{"label": "Look at the live chain", "fn": "info"}],
        "next": "two-kinds-of-address",
    },
    {
        "id": "two-kinds-of-address",
        "title": "Why you have two addresses that look nothing alike",
        "level": "start",
        "minutes": 3,
        "summary": "t-addresses are public, z-addresses are private, and "
                   "pasting one where the other belongs is the classic mistake.",
        "body": [
            "A TRANSPARENT address starts with t1 (or t3). It behaves exactly "
            "like a Bitcoin address: anyone can look it up in an explorer and "
            "see its balance and its entire history. This module can show you "
            "any t-address, including strangers'.",

            "A SHIELDED address starts with zs1. It has no public balance at "
            "all. If you paste a zs1 address into a block explorer, you learn "
            "nothing -- not because the explorer is being polite, but because "
            "the information genuinely is not on the chain in readable form. "
            "Only someone holding the matching key can see what was paid to "
            "it, and that person is you.",

            "There is a third form that starts with u1, a UNIFIED address. It "
            "is not a fourth kind of money; it is an envelope containing "
            "several addresses at once, so you can hand out one string and let "
            "the sender's wallet pick whichever kind it understands. Modern "
            "wallets prefer it.",

            "Here is the trap, and it is a real one: a unified address that "
            "contains both a shielded and a transparent receiver will often be "
            "paid TRANSPARENTLY, because the sender's software picks whatever "
            "is cheapest and simplest for it. You handed out what looked like "
            "a private address and received a public payment. This module's "
            "bridge deals with that by stripping the transparent receiver out "
            "before it hands your address to anyone -- see the bridging "
            "lesson.",

            "Practical rule: to receive privately, give out the zs1 form, or a "
            "unified address you know is shielded-only. To receive from an "
            "exchange that only understands old addresses, give out the t1 "
            "form and accept that the payment is public.",
        ],
        "terms": ["t-address", "z-address", "unified address", "receiver"],
        "try": [
            {"label": "Decode any address", "fn": "validate",
             "args": {"addr": "t1KfLLnDdRvSjbdCbTKyEyEEo2vP2ZDXbYP"}},
            {"label": "See your own shielded address", "fn": "shielded_address",
             "args": {"name": "<your wallet>"}},
        ],
        "next": "your-wallet",
    },
    {
        "id": "your-wallet",
        "title": "Your wallet is twelve words",
        "level": "start",
        "minutes": 3,
        "summary": "Every address and key you will ever have is computed from "
                   "one phrase. Lose it and the money is gone; leak it and the "
                   "money is theirs.",
        "body": [
            "When you create a wallet here you get a SEED PHRASE: twelve or "
            "twenty-four ordinary English words. That phrase is not a password "
            "you can change. It is the wallet. Every transparent address, "
            "every shielded address and every key is derived from it by a "
            "fixed calculation, which is why you can restore the whole wallet "
            "on any other Zcash wallet software from the words alone.",

            "Two consequences follow, and both are absolute. If you lose the "
            "phrase, no one can recover your money -- there is no support "
            "line, no password reset, no company holding a copy. If someone "
            "else reads the phrase, they can take everything immediately and "
            "irreversibly, from anywhere, without touching your computer.",

            "Write it on paper. Do not photograph it, do not put it in a chat "
            "message, do not paste it into a website that offers to check it. "
            "This module encrypts the phrase on disk with the password you "
            "choose, and will only print it back to you when you ask with that "
            "password -- but a phrase on your screen is a phrase your screen "
            "recorder, your screenshot folder and anyone behind you can read.",

            "The password you set is not the seed phrase and does not protect "
            "you if the phrase leaks. It only stops someone with access to "
            "this machine's files from reading the phrase off the disk.",
        ],
        "terms": ["seed phrase", "BIP39", "derivation path"],
        "try": [
            {"label": "Create a wallet", "fn": "wallet_create",
             "args": {"name": "mine", "password": "<a password>"}, "guarded": True},
            {"label": "List wallets", "fn": "wallet_list"},
        ],
        "next": "sending",
    },
    {
        "id": "sending",
        "title": "Sending, fees, and the word BROADCAST",
        "level": "core",
        "minutes": 3,
        "summary": "Nothing here spends money until you pass broadcast=true. "
                   "Everything before that is a rehearsal.",
        "body": [
            "Every function in this module that can move money is a DRY RUN by "
            "default. You call it, it builds the entire real transaction, "
            "shows you the inputs it would spend and the fee it would pay -- "
            "and then throws it away. Only when you pass broadcast=true does "
            "it actually go to the network. The answer always says which of "
            "the two happened, in capital letters.",

            "Use that. Run the dry version, read the fee and the change "
            "address, then run it again for real. A Zcash payment cannot be "
            "cancelled, reversed or clawed back once it is in a block.",

            "Fees are small and mostly fixed. Zcash charges by the size and "
            "shape of the transaction under a rule called ZIP-317, which works "
            "out to a fraction of a cent for an ordinary payment. You do not "
            "need to bid for space the way you sometimes do on Bitcoin.",

            "A payment is 'confirmed' once it is in a block, and safer with "
            "each block after that. Blocks come about every 75 seconds. For "
            "small amounts one or two confirmations is fine; for large ones, "
            "wait for ten.",

            "One asymmetry worth knowing before you try: this module can send "
            "transparent ZEC entirely by itself, and cannot send shielded ZEC "
            "without help. The next lesson explains why, because it is the "
            "single most common source of 'why did that fail'.",
        ],
        "terms": ["broadcast", "dry run", "ZIP-317", "confirmation", "UTXO"],
        "try": [
            {"label": "Estimate a fee", "fn": "estimate_fee",
             "args": {"inputs": 1, "outputs": 2}},
            {"label": "Rehearse a send (dry run)", "fn": "send",
             "args": {"name": "<wallet>", "password": "<password>",
                      "to": "t1...", "amount": 0.01}, "guarded": True},
        ],
        "next": "shielded-notes",
    },
    {
        "id": "shielded-notes",
        "title": "How shielded money actually works",
        "level": "core",
        "minutes": 4,
        "summary": "Your shielded balance is a pile of encrypted notes that "
                   "only your key can find. Finding them is called scanning.",
        "body": [
            "Shielded ZEC is not stored as a balance next to your address. It "
            "is stored as NOTES: encrypted packets sitting in the blockchain, "
            "each one saying 'this much ZEC belongs to whoever holds this "
            "key'. Everybody has a copy of every note. Almost nobody can read "
            "any of them.",

            "So working out your own balance is a search. Your wallet walks "
            "through blocks and tries to decrypt every shielded output with "
            "your key. The ones that decrypt are yours; the rest are noise. "
            "This is called SCANNING, and it is why shielded wallets take a "
            "while to sync while transparent ones are instant. There is no "
            "shortcut -- a shortcut would be a privacy leak.",

            "Because of that, a shielded wallet has a BIRTHDAY: the block "
            "height it was created at. There is no point scanning blocks older "
            "than the wallet, and skipping them is the difference between a "
            "scan that takes seconds and one that takes hours.",

            "A VIEWING KEY is the read-only half of your key. Handing one to "
            "an accountant, an auditor or a piece of software lets them see "
            "every payment you have ever received without letting them spend a "
            "coin. It is genuinely useful and genuinely dangerous: a viewing "
            "key cannot lose your money, but it reveals your entire history "
            "permanently, and you cannot un-share it.",

            "This module implements both shielded pools for real -- Sapling, "
            "which most wallets use, and Orchard, the newer one -- deriving "
            "the keys, building the addresses and decrypting your notes in "
            "plain Python, checked against Zcash's own official test vectors. "
            "It also reads the v6 transaction format now live at the chain "
            "tip, which matters more than it sounds: before that layout was "
            "worked out, two thirds of the transactions in a recent block "
            "range were simply unreadable, and a note of yours inside one "
            "would have been missed.",
        ],
        "terms": ["note", "scanning", "birthday", "viewing key", "Sapling",
                  "nullifier"],
        "try": [
            {"label": "Scan for your notes", "fn": "shielded_scan",
             "args": {"name": "<wallet>", "password": "<password>", "blocks": 2000},
             "guarded": True},
            {"label": "Shielded balance", "fn": "shielded_balance",
             "args": {"name": "<wallet>", "password": "<password>"}, "guarded": True},
        ],
        "next": "why-cant-i-send-shielded",
    },
    {
        "id": "why-cant-i-send-shielded",
        "title": "Why this module can receive shielded but not spend it",
        "level": "core",
        "minutes": 3,
        "summary": "Spending a shielded note requires generating a proof. "
                   "Reading one does not. That asymmetry is the whole story.",
        "body": [
            "Receiving shielded ZEC means decrypting something someone else "
            "already put on the chain. That is ordinary cryptography and this "
            "module does it.",

            "Spending shielded ZEC means creating a zero-knowledge proof -- "
            "convincing the entire network that you own a note and are not "
            "double-spending it, while revealing neither which note nor how "
            "much. The proof system Sapling uses is called Groth16. Producing "
            "one takes specialised code and a large set of parameters, and "
            "this module does not have it. It will not fake it, and it will "
            "not quietly send your money transparently instead.",

            "There are two honest ways out, and the module supports both. The "
            "first: export your spending key and use a wallet that can prove "
            "-- Zashi, Ywallet or zingo. They import the key and your notes "
            "are simply there. The second: point ZCASH_RPC_URL at a "
            "zcashd or zebrad node you run, and the node does the proving "
            "while this module drives it.",

            "The same limit is why you cannot shield money here either: "
            "turning transparent ZEC into shielded ZEC creates a shielded "
            "output, and creating one needs the same proof. What this module "
            "*can* do without any of that is bridge value from another chain "
            "directly into your shielded address -- the solver on the other "
            "side produces the output. That is the next lesson.",

            "Both shielded pools are read here — Sapling and the newer "
            "Orchard — so the unified addresses this module hands out "
            "advertise both. That is the rule to keep in mind if you ever "
            "extend it: never advertise a pool you cannot decrypt, because a "
            "payment into one arrives, confirms, and shows up nowhere.",
        ],
        "terms": ["Groth16", "zk-SNARK", "spending key", "Orchard", "proving"],
        "try": [
            {"label": "What can this module do?", "fn": "capabilities"},
            {"label": "Export keys for a proving wallet", "fn": "shielded_export",
             "args": {"name": "<wallet>", "password": "<password>"}, "guarded": True},
        ],
        "next": "bridging",
    },
    {
        "id": "bridging",
        "title": "Bridging: how ZEC becomes ETH and back",
        "level": "core",
        "minutes": 4,
        "summary": "You pay a deposit address, a solver pays your destination. "
                   "Deadline, exact amount, refund address -- those three "
                   "fields are where money goes missing.",
        "body": [
            "Zcash cannot run smart contracts, so there is no bridge contract "
            "holding your ZEC. Instead there are SOLVER networks: you say what "
            "you want, someone quotes a price, and you are given a one-time "
            "DEPOSIT ADDRESS. You send to it; they send the other asset to "
            "your destination address on the other chain. This module uses "
            "NEAR Intents (about 35 chains) and Maya Protocol.",

            "A quote costs nothing and reserves nothing. Reserving gives you "
            "the deposit address and starts a clock. Three fields decide "
            "whether this works: the AMOUNT must match the quote (send less "
            "and it may be refunded, send more and the excess may not come "
            "back), the DEADLINE must not pass before your transaction "
            "confirms, and the REFUND ADDRESS is where the money returns if "
            "anything goes wrong. Set the refund address to something you "
            "control. It is the only safety net.",

            "The deposit address is on the chain you are paying FROM. Bridging "
            "out of ZEC, it is a Zcash t-address and this module can pay it "
            "for you in one step. Bridging into ZEC, it is an address on "
            "Ethereum or Solana or wherever, and you pay it from your wallet "
            "there.",

            "Track a swap by its deposit address at any time. A swap that has "
            "not been paid yet simply shows nothing deposited -- that is not "
            "an error.",

            "Maya works differently in one dangerous way: its deposits carry a "
            "MEMO that says what the swap is for. Send Maya a deposit without "
            "the memo, or with the memo altered, and the funds are lost. If "
            "that makes you nervous, use the NEAR Intents route, which has no "
            "memo.",
        ],
        "terms": ["solver", "deposit address", "refund address", "memo",
                  "slippage", "intent"],
        "try": [
            {"label": "Which chains?", "fn": "bridge_chains"},
            {"label": "Price a swap", "fn": "bridge_quote",
             "args": {"to_asset": "ETH", "amount": 1, "recipient": "0x...",
                      "refund_to": "t1..."}},
        ],
        "next": "private-bridging",
    },
    {
        "id": "private-bridging",
        "title": "Bridging privately, and the direction that cannot be",
        "level": "core",
        "minutes": 4,
        "summary": "Money can enter the shielded pool straight off a bridge. "
                   "It cannot leave one privately, and no wording changes that.",
        "body": [
            "Bridging INTO Zcash can land directly in your shielded address. "
            "The trick is the unified address: the router accepts one as a "
            "destination, and a unified address whose only receiver is "
            "shielded leaves the solver no transparent option. So the payment "
            "arrives as an encrypted note. No transparent hop, no second "
            "transaction, nothing to shield afterwards.",

            "That is the good route, and it is not always on offer. Whether a "
            "shielded address is a valid destination is the router's rule, and "
            "it has said both things: it has taken a unified address, and it "
            "has refused every shielded form while quoting the same swap to a "
            "transparent one. When it refuses, this module reserves nothing "
            "and offers the two-leg route instead -- bridge to a transparent "
            "address you own, then shield it -- and labels that answer "
            "unshielded, because the money really is in the open until the "
            "second step. Take a quote first: quotes are free and tell you "
            "which of the two you are being given.",

            "This module builds that address for you. If you give it your zs1 "
            "address it wraps it, because the router rejects a bare zs1. If "
            "you give it your wallet's normal u1 address -- which contains a "
            "transparent receiver as well -- it re-encodes it with the "
            "transparent receiver removed, and tells you it did. That removal "
            "is not cosmetic: without it, the solver would very likely pay you "
            "in the clear.",

            "What is still visible: everything on the other chain. The origin "
            "address that funded the swap, the amount, the timing. The solver "
            "knows both ends because you told it both. What becomes invisible "
            "is where the money went inside Zcash and what you do with it "
            "afterwards -- which is usually the part that matters.",

            "Bridging OUT of the shielded pool is a different story, and the "
            "honest version is: it cannot be private. To leave Zcash the value "
            "has to become transparent, because the solver's deposit address "
            "is an ordinary t-address. The amount is public at that moment, "
            "and anyone comparing the two chains at that timestamp can link it "
            "to your destination address. The shielded spend still hides which "
            "notes paid -- so the payment does not link back to how the money "
            "arrived -- but the exit itself is in the open.",

            "If you care about that link, do not do it in one motion. Unshield "
            "to a fresh t-address, wait, and bridge from there as an ordinary "
            "transparent bridge. Timing correlation is what catches people, "
            "not cryptography.",

            "And the practical catch: bridging out of shielded means spending "
            "a note, which needs the proof this module cannot make. Without a "
            "node it will still reserve the deposit address and tell you "
            "exactly what to pay from a proving wallet. With ZCASH_RPC_URL "
            "set, it drives the node and does it in one step.",
        ],
        "terms": ["unified address", "receiver", "unshielding", "timing "
                  "correlation", "solver"],
        "try": [
            {"label": "What shielded routes work now?", "fn": "bridge_shielded_plan"},
            {"label": "Quote a private inbound bridge", "fn": "bridge_shielded_in",
             "args": {"from_asset": "eth:USDC", "amount": 100,
                      "recipient": "zs1... or u1...", "refund_to": "0x..."}},
        ],
        "next": "staying-safe",
    },
    {
        "id": "staying-safe",
        "title": "The five ways people actually lose money",
        "level": "start",
        "minutes": 3,
        "summary": "None of them are cryptography failures.",
        "body": [
            "One: they leak the seed phrase. Into a screenshot, a password "
            "manager sync, a support chat, a 'wallet validator' website. The "
            "phrase is the money. Nobody legitimate will ever ask for it.",

            "Two: they paste the wrong address. Zcash addresses have "
            "checksums, so a typo is usually caught -- but a *valid address "
            "belonging to someone else*, copied from the wrong line or swapped "
            "by clipboard malware, is not. Check the first and last five "
            "characters after pasting, every time. Use the validate function "
            "if you are unsure what you are holding.",

            "Three: they skip the dry run. This module shows you the whole "
            "transaction before it sends it, for free. Reading that output "
            "costs seconds and catches wrong amounts, wrong recipients and "
            "surprising fees.",

            "Four: they miss a bridge deadline or drop a memo. A solver swap "
            "is a contract with a clock. Send the exact amount, before the "
            "deadline, with the memo if the route uses one, and always set a "
            "refund address you control.",

            "Five: they assume shielded means invisible everywhere. It hides "
            "what is on the Zcash chain. It does not hide what an exchange "
            "knows, what the other chain shows, or that you moved a distinctive "
            "amount at a distinctive moment. Privacy is a habit, not a "
            "checkbox.",

            "One more, specific to this module: the spending token. Reading is "
            "open; anything that can move funds or reveal a key needs the token "
            "from ~/.mod/zcash/server.secret. Treat it like a password, and do "
            "not paste it into a page you did not open yourself.",
        ],
        "terms": ["seed phrase", "checksum", "dry run", "refund address"],
        "try": [
            {"label": "Check an address before you use it", "fn": "validate",
             "args": {"addr": "<paste it here>"}},
        ],
        "next": "reading-the-chain",
    },
    {
        "id": "reading-the-chain",
        "title": "Reading the chain: what an explorer can and cannot tell you",
        "level": "deep",
        "minutes": 3,
        "summary": "A transaction shows you its transparent half exactly and "
                   "its shielded half only as a shape.",
        "body": [
            "Look up a transaction and you get value in, value out, the fee, "
            "and -- if part of it is shielded -- how many shielded spends and "
            "outputs it contains and the net value that moved into or out of "
            "the pool. That net value is public on purpose: the network has to "
            "check that no ZEC was invented, so money crossing the boundary is "
            "always visible even when its destination is not.",

            "What you cannot get is the amount or recipient of a shielded "
            "output. Those are encrypted. If one of them is yours, your "
            "viewing key opens it and nothing else does.",

            "A fully shielded transaction -- shielded in, shielded out -- "
            "shows a fee and a shape and nothing else. That is what people "
            "mean when they say Zcash has real privacy: not that it is hidden "
            "from an explorer, but that there is nothing there to find.",

            "Watch out for one number: circulating supply from public "
            "explorers is often wrong for Zcash, sometimes impossibly so (a "
            "negative figure). This module range-checks it against the 21M cap "
            "and says where its answer came from.",
        ],
        "terms": ["shielded pool", "value balance", "explorer"],
        "try": [
            {"label": "Open a transaction", "fn": "tx", "args": {"txid": "<txid>"}},
            {"label": "Network health", "fn": "network"},
        ],
        "next": "using-this-module",
    },
    {
        "id": "using-this-module",
        "title": "Using this module from code or an agent",
        "level": "deep",
        "minutes": 3,
        "summary": "One set of functions, reachable four ways, with one gate.",
        "body": [
            "Everything here is a named function taking JSON. The same "
            "functions are reachable through the mod protocol, through this "
            "module's local REST API, as MCP tools for an AI client, and "
            "through the console you are probably reading this in. They are "
            "literally the same code -- a tool and a route cannot disagree.",

            "Reads are open: chain data, prices, address lookups, quotes, and "
            "these lessons. Anything that spends, deletes, reserves, reveals a "
            "seed or decrypts a note needs a bearer token, printed by "
            "'m zcash/token'. Shielded reading is gated too, which surprises "
            "people -- but a viewing key exposes your entire payment history, "
            "so it is not a public read.",

            "For an AI client, point it at the MCP endpoint and it gets the "
            "whole module as tools with the same gate. The ask function is a "
            "smaller thing that lives inside this module: it answers questions "
            "from these lessons, runs the read-only functions needed to ground "
            "an answer in live data, and writes out the exact call you would "
            "make next without making it for you.",
        ],
        "terms": ["MCP", "bearer token", "mod protocol"],
        "try": [
            {"label": "Ask a question", "fn": "ask",
             "args": {"question": "how do I bridge USDC into shielded ZEC?"}},
            {"label": "List the MCP tools", "fn": "mcp"},
        ],
        "next": None,
    },
]

LESSON_INDEX = {lesson["id"]: lesson for lesson in LESSONS}

PATHS = {
    "beginner": ["what-is-zcash", "two-kinds-of-address", "your-wallet",
                 "staying-safe"],
    "sending": ["your-wallet", "sending", "staying-safe"],
    "privacy": ["two-kinds-of-address", "shielded-notes",
                "why-cant-i-send-shielded", "private-bridging"],
    "bridging": ["bridging", "private-bridging", "staying-safe"],
    "developer": ["using-this-module", "why-cant-i-send-shielded",
                  "reading-the-chain"],
}


# ── Glossary ────────────────────────────────────────────────────────────────
#
# One or two sentences each, in the same voice as the lessons. `see` points at
# the lesson that puts the term in context.

GLOSSARY = {
    "zec": ("The unit of Zcash currency. 1 ZEC is 100,000,000 zatoshi, the "
            "same way a bitcoin is 100,000,000 satoshi. Amounts on the chain "
            "are always whole zatoshi.", "what-is-zcash"),
    "zatoshi": ("The smallest unit of ZEC: one hundred-millionth. Functions "
                "that return exact amounts use zatoshi to avoid rounding.",
                "what-is-zcash"),
    "transparent": ("The public side of Zcash. Transparent addresses, "
                    "balances and payments are visible to everyone forever, "
                    "exactly like Bitcoin.", "two-kinds-of-address"),
    "shielded": ("The private side of Zcash. Shielded payments are encrypted "
                 "on the chain; only a key holder can read them. Validity is "
                 "still checked by everyone, via zero-knowledge proofs.",
                 "what-is-zcash"),
    "t-address": ("A transparent address, starting t1 (or t3 for multisig-"
                  "style scripts). Public balance, public history.",
                  "two-kinds-of-address"),
    "z-address": ("A shielded address, starting zs1. No public balance -- "
                  "there is nothing for an explorer to show.",
                  "two-kinds-of-address"),
    "unified address": ("An address starting u1 that packages several "
                        "receivers in one string, so any wallet can pay it. "
                        "Careful: if it contains a transparent receiver, a "
                        "sender may use that one and pay you publicly.",
                        "two-kinds-of-address"),
    "receiver": ("One address inside a unified address. A unified address "
                 "might carry a Sapling receiver and a transparent receiver; "
                 "the sender picks.", "two-kinds-of-address"),
    "sapling": ("The shielded pool most Zcash wallets use today, and the one "
                "this module implements. Introduced in 2018.",
                "shielded-notes"),
    "orchard": ("The newer shielded pool, introduced with NU5. Faster and "
                "simpler than Sapling, and read here too — the unified "
                "addresses this module hands out advertise both pools.",
                "shielded-notes"),
    "sprout": ("The original, long-deprecated shielded pool. Cannot receive "
               "funds. If you find one, move on.", "two-kinds-of-address"),
    "note": ("One packet of shielded money on the chain -- an encrypted "
             "record saying an amount belongs to whoever holds a key. Your "
             "shielded balance is the sum of your unspent notes.",
             "shielded-notes"),
    "scanning": ("Walking through blocks trying to decrypt every shielded "
                 "output with your key, to find the ones that are yours. This "
                 "is why shielded wallets sync slowly.", "shielded-notes"),
    "birthday": ("The block height a wallet was created at. Scanning can "
                 "safely skip everything before it.", "shielded-notes"),
    "viewing key": ("The read-only half of a shielded key. Lets someone see "
                    "every payment you received without spending anything. "
                    "Useful for audits, impossible to un-share.",
                    "shielded-notes"),
    "spending key": ("The secret that can spend your shielded notes. Anyone "
                     "holding it holds the money.", "why-cant-i-send-shielded"),
    "nullifier": ("A one-way tag published when a shielded note is spent, so "
                  "the network can reject double-spends without learning "
                  "which note was spent.", "shielded-notes"),
    "zero-knowledge proof": ("A proof that a statement is true which reveals "
                             "nothing except that it is true. Zcash uses it "
                             "to prove a payment is valid without revealing "
                             "sender, recipient or amount.", "what-is-zcash"),
    "zk-snark": ("The specific family of zero-knowledge proof Zcash uses: "
                 "small and fast to check, expensive to produce.",
                 "why-cant-i-send-shielded"),
    "groth16": ("The proof system behind Sapling spends. Producing one needs "
                "specialised code and parameters this module does not carry, "
                "which is exactly why it cannot spend shielded ZEC.",
                "why-cant-i-send-shielded"),
    "proving": ("Generating the zero-knowledge proof a shielded spend needs. "
                "Done here by a node you configure, or by a proving wallet "
                "you export your key into.", "why-cant-i-send-shielded"),
    "shielding": ("Moving transparent ZEC into the shielded pool. It creates "
                  "a shielded output, so it needs proving too.",
                  "why-cant-i-send-shielded"),
    "unshielding": ("Moving shielded ZEC back out into a transparent address. "
                    "The amount becomes public at that moment.",
                    "private-bridging"),
    "shielded pool": ("All the shielded value on the chain, taken together. "
                      "Money entering or leaving it is publicly visible; "
                      "movement inside it is not.", "reading-the-chain"),
    "value balance": ("The net amount a transaction moved into or out of the "
                      "shielded pool. Public by necessity, so the network can "
                      "check no money was invented.", "reading-the-chain"),
    "seed phrase": ("Twelve or twenty-four words that ARE your wallet. Every "
                    "key is computed from them. Lose it, lose everything; "
                    "leak it, lose everything.", "your-wallet"),
    "bip39": ("The standard that turns a word list into a wallet seed. It is "
              "why your phrase restores in other wallets too.",
              "your-wallet"),
    "derivation path": ("The recipe that turns one seed into many addresses, "
                        "like m/44'/133'/0'/0/0. 133 is Zcash's number.",
                        "your-wallet"),
    "utxo": ("An unspent chunk of transparent coin. Transparent payments "
             "consume whole chunks and pay change back to you, like cash.",
             "sending"),
    "dry run": ("Building a real transaction and then not sending it. The "
                "default for everything here that can move money.", "sending"),
    "broadcast": ("Actually publishing a transaction to the network. In this "
                  "module it is an explicit argument you must pass; without "
                  "it, nothing moves.", "sending"),
    "confirmation": ("A block containing your transaction. More blocks on top "
                     "means harder to reverse. Zcash blocks come about every "
                     "75 seconds.", "sending"),
    "zip-317": ("The rule that sets Zcash fees from the size and shape of a "
                "transaction. Fractions of a cent for ordinary payments.",
                "sending"),
    "checksum": ("Extra characters inside an address that let software catch "
                 "a typo. It cannot catch a valid address belonging to "
                 "someone else.", "staying-safe"),
    "explorer": ("A site or API that reads the public chain for you. It can "
                 "show you a transparent address in full and a shielded one "
                 "not at all, because the second one is not public data.",
                 "reading-the-chain"),
    "solver": ("Whoever actually performs a cross-chain swap: you pay their "
               "deposit address, they pay your destination. Bridges here are "
               "solver networks, not contracts.", "bridging"),
    "intent": ("A statement of what you want swapped, which solvers compete "
               "to fill. The model behind the NEAR Intents route.",
               "bridging"),
    "deposit address": ("The one-time address a bridge tells you to pay. Send "
                        "the exact amount before the deadline.", "bridging"),
    "refund address": ("Where a failed swap returns your money. Set it to "
                       "something you control -- it is the only safety net.",
                       "bridging"),
    "memo": ("A short message attached to a payment. Shielded payments can "
             "carry one privately; Maya bridge deposits REQUIRE one, and "
             "getting it wrong loses the funds.", "bridging"),
    "slippage": ("How far the price may move before a swap gives up. Quoted "
                 "in basis points; 100 means one percent.", "bridging"),
    "timing correlation": ("Linking two events because they happened close "
                           "together and for the same amount. The usual way "
                           "shielded privacy is lost in practice, without "
                           "breaking any cryptography.", "private-bridging"),
    "mcp": ("Model Context Protocol: how an AI client discovers and calls "
            "this module's functions as tools.", "using-this-module"),
    "bearer token": ("The secret in ~/.mod/zcash/server.secret that unlocks "
                     "spending, key export and shielded reads on this "
                     "module's API.", "using-this-module"),
    "mod protocol": ("The fleet convention this module is built for: named "
                     "functions taking JSON, reachable at /{module} and "
                     "/api/{module}.", "using-this-module"),
    "nu5": ("The 2022 network upgrade that introduced Orchard and the "
            "transaction format this module builds.", "reading-the-chain"),
    "consensus branch id": ("A number identifying which network upgrade the "
                            "chain is on. A transaction signed for the wrong "
                            "one is rejected.", "reading-the-chain"),
}

# Words that mean the same thing as a glossary key, so a beginner's phrasing
# still lands: someone asks about "z address", not "z-address".
ALIASES = {
    "z addr": "z-address", "zaddr": "z-address", "z address": "z-address",
    "zs1": "z-address", "shielded address": "z-address",
    "t addr": "t-address", "taddr": "t-address", "t address": "t-address",
    "t1": "t-address", "transparent address": "t-address",
    "u1": "unified address", "ua": "unified address",
    "unified": "unified address", "unified addresses": "unified address",
    "seed": "seed phrase", "mnemonic": "seed phrase",
    "recovery phrase": "seed phrase", "words": "seed phrase",
    "private key": "spending key", "spend key": "spending key",
    "ivk": "viewing key", "fvk": "viewing key", "full viewing key": "viewing key",
    "notes": "note", "scan": "scanning", "sync": "scanning",
    "fee": "zip-317", "fees": "zip-317", "gas": "zip-317",
    "confirmations": "confirmation", "block": "confirmation",
    "snark": "zk-snark", "zksnark": "zk-snark", "zk": "zero-knowledge proof",
    "proof": "zero-knowledge proof", "zkp": "zero-knowledge proof",
    "bridge": "solver", "bridges": "solver", "bridging": "solver",
    "swap": "solver", "1click": "solver", "near intents": "solver",
    "shield": "shielding", "unshield": "unshielding",
    "token": "bearer token", "auth": "bearer token", "password": "bearer token",
    "privacy": "shielded", "private": "shielded", "anonymous": "shielded",
    "public": "transparent", "sat": "zatoshi", "satoshi": "zatoshi",
    "wallet": "seed phrase", "restore": "seed phrase", "backup": "seed phrase",
}


def lessons(level: str = None, path: str = None) -> dict:
    """The lesson list, optionally filtered to a level or a reading path."""
    if path:
        key = path.strip().lower()
        if key not in PATHS:
            raise KeyError(f"unknown path {path!r}; have {', '.join(PATHS)}")
        chosen = [LESSON_INDEX[i] for i in PATHS[key]]
    elif level:
        chosen = [l for l in LESSONS if l["level"] == level.strip().lower()]
    else:
        chosen = LESSONS
    return {
        "count": len(chosen),
        "levels": ["start", "core", "deep"],
        "paths": {k: [LESSON_INDEX[i]["title"] for i in v] for k, v in PATHS.items()},
        "lessons": [{k: v for k, v in l.items() if k != "body"} for l in chosen],
        "reading_time_minutes": sum(l["minutes"] for l in chosen),
    }


def lesson(topic: str) -> dict:
    """One lesson in full, by id or by a fuzzy match on its title."""
    key = (topic or "").strip().lower().replace(" ", "-")
    if key in LESSON_INDEX:
        return dict(LESSON_INDEX[key])
    words = set(_words(topic))
    scored = []
    for l in LESSONS:
        hay = set(_words(l["title"] + " " + l["summary"] + " " + " ".join(l["terms"])))
        overlap = len(words & hay)
        if overlap:
            scored.append((overlap, l))
    if not scored:
        raise KeyError(
            f"no lesson matches {topic!r}; have {', '.join(LESSON_INDEX)}")
    scored.sort(key=lambda s: -s[0])
    return dict(scored[0][1])


def explain(term: str) -> dict:
    """Define one word, with the lesson that puts it in context."""
    raw = (term or "").strip().lower()
    if not raw:
        raise KeyError("no term given")
    key = ALIASES.get(raw, raw)
    if key not in GLOSSARY:
        # Try the singular, then a substring match, then give up with options.
        singular = key[:-1] if key.endswith("s") else key
        key = ALIASES.get(singular, singular)
    if key not in GLOSSARY:
        near = [k for k in GLOSSARY if raw in k or k in raw]
        if len(near) == 1:
            key = near[0]
        else:
            raise KeyError(
                f"no glossary entry for {term!r}"
                + (f"; did you mean {', '.join(sorted(near)[:5])}?" if near else "")
                + f" -- {len(GLOSSARY)} terms are defined, list them with "
                  "learn(glossary=True)")
    text, see = GLOSSARY[key]
    out = {"term": key, "definition": text}
    if raw != key:
        out["asked"] = raw
    if see:
        ref = LESSON_INDEX[see]
        out["see_also"] = {"lesson": see, "title": ref["title"],
                           "summary": ref["summary"]}
    return out


def glossary() -> dict:
    return {
        "count": len(GLOSSARY),
        "terms": [{"term": k, "definition": v[0], "lesson": v[1]}
                  for k, v in sorted(GLOSSARY.items())],
    }


def _words(text: str) -> list:
    out, word = [], []
    for ch in (text or "").lower():
        if ch.isalnum():
            word.append(ch)
        elif word:
            out.append("".join(word))
            word = []
    if word:
        out.append("".join(word))
    return out
