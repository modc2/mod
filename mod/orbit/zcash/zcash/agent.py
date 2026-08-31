"""
An agent that answers Zcash questions inside this module.

The design decision worth explaining: this does not need a language model to
work. A model can be attached (set ZCASH_LLM_URL to any OpenAI-compatible
/chat/completions endpoint) and it makes the prose better, but the module ships
useful without one, because a hosted key is not a thing every deployment has
and "the docs agent is down" is a bad failure for the surface whose whole job
is explaining how not to lose money.

What it does instead, in order:

  1. INTENT. A small set of hand-written intents recognise the questions people
     actually ask -- "how do I bridge privately", "why can't I send shielded",
     "is my z-address safe to share". Each intent knows which lesson answers
     it, which live read grounds it, and which function you would call next.
  2. GROUNDING. Read-only functions are actually called, so the answer carries
     today's price, this wallet's real address, this deployment's real
     capabilities. Never a guarded function: the agent will write out a call
     that spends, and will not make it.
  3. RETRIEVAL. Anything no intent claims falls back to scoring the lessons and
     glossary against the question, which is a decent answer because the corpus
     is small, hand-written and about exactly one subject.
  4. MODEL, optionally. If an endpoint is configured, everything above is
     handed to it as context and it writes the prose. The citations and the
     suggested calls still come from step 1-3, so a hallucinated function name
     cannot reach the user as a button.

The agent never invents a function name. `actions` are checked against the
module's own function list before they go out.
"""

import json
import os
import urllib.error
import urllib.request

try:
    from . import learn as _learn
except ImportError:  # loaded as a loose module
    import learn as _learn

# Functions the agent may call by itself. Read-only, no secrets, no spending.
# Deliberately not "everything in OPEN_FNS": wallet_balance is open on the REST
# API because a local operator asked for it, but an agent volunteering someone's
# balance into an answer is a different thing.
GROUNDING_FNS = {
    "info", "price", "network", "capabilities", "validate", "estimate_fee",
    "bridge_chains", "bridge_shielded_plan", "mempool",
}

_LLM_URL = "ZCASH_LLM_URL"
_LLM_KEY = "ZCASH_LLM_KEY"
_LLM_MODEL = "ZCASH_LLM_MODEL"


class AgentError(Exception):
    pass


# ── Intents ─────────────────────────────────────────────────────────────────
#
# `any` fires when a question mentions any phrase; `all` requires every group
# to be represented (each group is itself a list of synonyms). Scoring prefers
# the intent with the most matched phrases, so a vague question falls through
# to retrieval rather than getting a confidently wrong specific answer.

INTENTS = [
    {
        "id": "bridge-in-shielded",
        # Three groups, and the third is what makes this intent about a
        # DIRECTION rather than about bridging in general: without an inbound
        # phrase, "swap my shielded ZEC for ETH" would land here too.
        "all": [["bridge", "swap", "convert", "move", "buy", "get", "receive",
                 "deposit", "on ramp", "onramp"],
                ["shielded", "private", "privately", "z-address", "zaddr",
                 "z address", "anonymous", "zs1", "u1", "unified"],
                ["into zcash", "into zec", "to zcash", "to zec", "buy zec",
                 "into shielded", "into the shielded", "into a shielded",
                 "into my shielded", "to a shielded", "to my shielded",
                 "into the pool", "for zec", "shielded address"]],
        "lesson": "private-bridging",
        "answer": (
            "You can bridge into the shielded pool directly -- the funds arrive "
            "as an encrypted note, with no transparent hop and no second "
            "transaction. Give bridge_shielded_in your own shielded address as "
            "the recipient (zs1 or u1, either is fine) and a refund address on "
            "the chain you are paying FROM. It rewrites your address into a "
            "shielded-only unified address before handing it to the router, "
            "because a unified address that still carries a transparent "
            "receiver would very likely be paid in the clear.\n\n"
            "The quote costs nothing. Pass reserve=true to get a real deposit "
            "address, then pay that address from your wallet on the origin "
            "chain. What stays visible is everything on that side: the origin "
            "address, the amount and the timing. What becomes private is where "
            "the money went inside Zcash."),
        "ground": ["bridge_shielded_plan"],
        "actions": [
            {"label": "See which shielded routes work right now",
             "fn": "bridge_shielded_plan"},
            {"label": "Quote it (nothing reserved)", "fn": "bridge_shielded_in",
             "args": {"from_asset": "eth:USDC", "amount": 100,
                      "recipient": "<your zs1 or u1 address>",
                      "refund_to": "<your address on the origin chain>"}},
        ],
    },
    {
        "id": "bridge-out-shielded",
        "all": [["bridge", "swap", "convert", "sell", "cash out", "cashout",
                 "off ramp", "offramp", "exit", "withdraw", "move", "spend"],
                ["shielded", "private", "privately", "z-address", "zaddr",
                 "zs1", "notes", "pool"],
                ["out of", "out to", "zec out", "shielded out", "pool out",
                 "from shielded", "from my shielded", "from the shielded",
                 "from a shielded", "sell zec", "zec for", "zec to",
                 "for eth", "for btc", "for usdc", "to ethereum", "to eth",
                 "to btc", "to solana", "to usdc", "leave", "back to"]],
        "lesson": "private-bridging",
        "answer": (
            "Bridging out of the shielded pool works, but it cannot be "
            "private, and two separate things stand in the way.\n\n"
            "First, privacy: the solver's deposit address is an ordinary "
            "t-address, so the value has to become transparent to leave Zcash "
            "at all. The amount is public at that moment and links to your "
            "destination address by timing. The shielded spend still hides "
            "WHICH notes paid, so it does not link back to how the money "
            "arrived -- but the exit is in the open. If that matters, unshield "
            "to a fresh t-address, wait, and bridge from there separately.\n\n"
            "Second, mechanics: spending a shielded note needs a Groth16 "
            "proof, which this module cannot produce. With ZCASH_RPC_URL "
            "pointed at your own node, bridge_shielded_out drives the node and "
            "does it in one step. Without one, it still reserves the deposit "
            "address and hands you the exact amount and deadline to pay from a "
            "proving wallet (Zashi, Ywallet, zingo) using shielded_export."),
        "ground": ["bridge_shielded_plan", "capabilities"],
        "actions": [
            {"label": "Check whether a proving node is configured",
             "fn": "capabilities"},
            {"label": "Plan the exit", "fn": "bridge_shielded_out",
             "args": {"name": "<wallet>", "password": "<password>",
                      "to_asset": "ETH", "amount": 1,
                      "recipient": "<your 0x address>"}, "guarded": True},
        ],
    },
    {
        "id": "cannot-send-shielded",
        "any": ["can't send shielded", "cannot send shielded", "shielded send",
                "why can't i send", "spend shielded", "send from my z",
                "groth16", "proving", "shield my", "shielding"],
        "lesson": "why-cant-i-send-shielded",
        "answer": (
            "Because receiving and spending are not symmetric. Receiving means "
            "decrypting something already on the chain -- ordinary "
            "cryptography, and this module does it for real. Spending means "
            "producing a zero-knowledge proof (Groth16) that you own a note "
            "and are not double-spending it, without revealing which note. "
            "That needs specialised proving code this module does not carry, "
            "and it will not fake it or quietly send transparently instead.\n\n"
            "Two honest ways out. Export the spending key with "
            "shielded_export and import it into Zashi, Ywallet or zingo, which "
            "can prove -- your notes are simply there. Or point ZCASH_RPC_URL "
            "at a zcashd/zebrad node and let it do the proving while this "
            "module drives it via shielded_send.\n\n"
            "The same limit means you cannot SHIELD transparent funds here "
            "either, since that also creates a shielded output. The exception "
            "is bridging in from another chain: there the solver creates the "
            "output, so bridge_shielded_in lands money in your shielded "
            "address without any proving on your side. Both pools are read "
            "here, Sapling and Orchard, so either can receive it."),
        "ground": ["capabilities"],
        "actions": [
            {"label": "What can this module do?", "fn": "capabilities"},
            {"label": "Export keys for a proving wallet", "fn": "shielded_export",
             "args": {"name": "<wallet>", "password": "<password>"},
             "guarded": True},
        ],
    },
    {
        "id": "which-address",
        "any": ["which address", "what address", "t1 or z", "t1 and z",
                "t or z", "z or t", "difference between t", "t1 vs",
                "t address vs", "transparent vs", "shielded vs",
                "zs1 vs", "u1 vs", "zs1 and", "t1 and", "kinds of address",
                "types of address", "what is a z address",
                "what is a t address", "unified address", "which one should i use"],
        "lesson": "two-kinds-of-address",
        "answer": (
            "t1 is public: anyone can look it up and see its balance and full "
            "history, exactly like Bitcoin. zs1 is shielded: it has no public "
            "balance at all, and only your key can see what was paid to it. "
            "u1 is a unified address, which is an envelope containing several "
            "of the others so any wallet can pay it.\n\n"
            "The trap is u1. If it contains a transparent receiver as well as "
            "a shielded one, the sender's software will often pick the "
            "transparent one -- you handed out what looked like a private "
            "address and got a public payment. Your wallet's default u1 here "
            "is exactly that shape.\n\n"
            "So: to receive privately, hand out the zs1 form. To receive from "
            "an exchange that only understands old addresses, hand out t1 and "
            "accept that it is public. For bridging, the module strips the "
            "transparent receiver for you automatically."),
        "actions": [
            {"label": "Decode an address you are holding", "fn": "validate",
             "args": {"addr": "<paste the address>"}},
            {"label": "Show my shielded address", "fn": "shielded_address",
             "args": {"name": "<wallet>"}},
        ],
    },
    {
        "id": "safe-to-share",
        "any": ["safe to share", "safe to give", "can i share", "is it safe",
                "share my address", "viewing key safe", "give someone",
                "post my address"],
        "lesson": "shielded-notes",
        "answer": (
            "Addresses are safe to share -- all of them. A t-address reveals "
            "your public history to whoever you give it to, which is a privacy "
            "cost but not a theft risk. A z-address reveals nothing at all.\n\n"
            "A VIEWING KEY is a different matter. It cannot spend a coin, so "
            "your money is safe, but it exposes every shielded payment you "
            "have ever received, permanently, and you cannot un-share it. Give "
            "one to an auditor on purpose, never casually.\n\n"
            "Never share: the seed phrase and the spending key. Either one IS "
            "the money. And do not paste this module's bearer token into "
            "anything you did not open yourself -- it unlocks spending and key "
            "export on this API."),
        "actions": [
            {"label": "See what your addresses reveal", "fn": "shielded_address",
             "args": {"name": "<wallet>"}},
        ],
    },
    {
        "id": "lost-seed",
        "any": ["lost my seed", "forgot my seed", "lost my phrase",
                "forgot password", "recover my wallet", "restore",
                "lost my words", "backup"],
        "lesson": "your-wallet",
        "answer": (
            "The seed phrase is the only recovery mechanism that exists. If "
            "you have the words you can restore the whole wallet here or in "
            "any other Zcash wallet, with wallet_restore. If you have lost "
            "them and the wallet file too, the money is unreachable -- there "
            "is no support line and no reset, by design.\n\n"
            "If you still have the wallet on this machine and only forgot the "
            "PASSWORD, that is also unrecoverable: the password is what "
            "encrypts the seed on disk, and there is no backdoor around it.\n\n"
            "While you still have access, write the phrase on paper. Not a "
            "screenshot, not a chat message, not a site offering to check it."),
        "actions": [
            {"label": "Restore from a phrase", "fn": "wallet_restore",
             "args": {"name": "<name>", "password": "<new password>",
                      "mnemonic": "<your twelve words>"}, "guarded": True},
            {"label": "List wallets on this machine", "fn": "wallet_list"},
        ],
    },
    {
        "id": "how-private",
        "any": ["how private", "is zcash private", "really private",
                "can they trace", "traceable", "anonymous", "does it hide",
                "what leaks", "deanonym"],
        "lesson": "private-bridging",
        "answer": (
            "Shielded Zcash hides what is on the Zcash chain: a fully shielded "
            "transaction shows a fee and a shape, and there is genuinely "
            "nothing else there to find. That part is real cryptography, not a "
            "policy.\n\n"
            "What it does not hide: anything off that chain. The exchange that "
            "sold you the ZEC knows. The other chain in a bridge shows your "
            "origin address, amount and timing in the clear. Money ENTERING or "
            "LEAVING the shielded pool is public by design -- the network has "
            "to check no ZEC was invented -- so only movement inside the pool "
            "is hidden.\n\n"
            "In practice people lose privacy to timing and amounts, not to "
            "broken maths: distinctive amounts moved minutes apart link "
            "together across chains without decrypting anything. Bridge "
            "unmemorable amounts, and let value sit in the pool before you "
            "move it again."),
        "ground": ["bridge_shielded_plan"],
        "actions": [
            {"label": "What each bridge direction leaks",
             "fn": "bridge_shielded_plan"},
        ],
    },
    {
        "id": "fees",
        "any": ["fee", "fees", "how much does it cost", "gas", "expensive",
                "cheap", "zip-317", "cost to send"],
        "lesson": "sending",
        "answer": (
            "Zcash fees are small and mostly fixed. They are set by the size "
            "and shape of the transaction under a rule called ZIP-317 -- "
            "typically a fraction of a cent for an ordinary payment. You do "
            "not bid for block space the way you sometimes do on Bitcoin.\n\n"
            "Bridge costs are a different thing entirely and much larger: the "
            "solver's spread plus the fee on the destination chain. A bridge "
            "quote shows both sides in USD, so compare amount_in_usd against "
            "amount_out_usd rather than looking at the Zcash fee."),
        "ground": ["estimate_fee", "price"],
        "actions": [
            {"label": "Estimate a real fee", "fn": "estimate_fee",
             "args": {"inputs": 1, "outputs": 2}},
        ],
    },
    {
        "id": "getting-started",
        "any": ["get started", "getting started", "how do i start", "new to",
                "beginner", "first time", "where do i begin", "explain zcash",
                "what is zcash", "teach me"],
        "lesson": "what-is-zcash",
        "answer": (
            "Zcash is Bitcoin-shaped money with an optional invisibility "
            "cloak. Same public ledger, same 21 million cap -- plus a second, "
            "private way to hold the same coins, where the chain records that "
            "a valid payment happened without recording who, whom or how "
            "much.\n\n"
            "Start here, in order: 'What Zcash actually is', then 'Why you "
            "have two addresses that look nothing alike', then 'Your wallet is "
            "twelve words', then 'The five ways people actually lose money'. "
            "That is about ten minutes and covers everything you need before "
            "touching real funds.\n\n"
            "Then make a wallet and look at your own addresses. Reading about "
            "a shielded address and looking at yours are different kinds of "
            "understanding."),
        "ground": ["info"],
        "actions": [
            {"label": "Read the beginner path", "fn": "learn",
             "args": {"path": "beginner"}},
            {"label": "Make a wallet", "fn": "wallet_create",
             "args": {"name": "mine", "password": "<a password>"},
             "guarded": True},
        ],
    },
    {
        "id": "dry-run",
        "any": ["dry run", "broadcast", "did it send", "nothing happened",
                "no transaction", "didn't send", "not sent", "test send"],
        "lesson": "sending",
        "answer": (
            "It did not send, and that is the default. Every function here "
            "that can move money builds the real transaction and then throws "
            "it away unless you pass broadcast=true. The response says which "
            "happened, in capital letters: DRY RUN or BROADCAST.\n\n"
            "That is deliberate -- a Zcash payment cannot be reversed. Read "
            "the dry run's inputs, fee and change address, then run the same "
            "call again with broadcast=true."),
        "actions": [
            {"label": "Rehearse a send", "fn": "send",
             "args": {"name": "<wallet>", "password": "<password>",
                      "to": "t1...", "amount": 0.01, "broadcast": False},
             "guarded": True},
        ],
    },
    {
        "id": "balance-missing",
        "any": ["balance is zero", "no balance", "where is my money",
                "not showing", "can't see my funds", "shielded balance empty",
                "scan found nothing", "missing funds"],
        "lesson": "shielded-notes",
        "answer": (
            "For a shielded balance, 'zero' usually means 'not scanned yet'. "
            "Shielded money is stored as encrypted notes and finding yours "
            "means decrypting candidates block by block, so the balance only "
            "appears after a scan covers the blocks the payment landed in. "
            "Scan a wider range with shielded_scan(blocks=...) and make sure "
            "the range includes the height the payment arrived at.\n\n"
            "Also expect unspent_zec to be null rather than 0 without a node: "
            "detecting which of your notes have been SPENT needs note "
            "positions from the commitment tree, which only a node can give. "
            "Received totals are exact either way.\n\n"
            "For a transparent balance, zero after a send is usually change "
            "landing on a different address of the same wallet -- check "
            "wallet_balance, which totals every address, not one."),
        "actions": [
            {"label": "Scan a wider range", "fn": "shielded_scan",
             "args": {"name": "<wallet>", "password": "<password>",
                      "blocks": 4000}, "guarded": True},
            {"label": "Total every address", "fn": "wallet_balance",
             "args": {"name": "<wallet>"}},
        ],
    },
    {
        "id": "bridge-basics",
        "any": ["bridge", "swap", "cross-chain", "cross chain", "to ethereum",
                "to eth", "to btc", "to solana", "usdc", "which chains"],
        "lesson": "bridging",
        "answer": (
            "There is no bridge contract -- Zcash has no smart contracts. "
            "Instead a solver network quotes you a price and gives you a "
            "one-time deposit address; you pay it, they pay your destination "
            "on the other chain. This module uses NEAR Intents (about 35 "
            "chains) and Maya.\n\n"
            "Three fields decide whether it works. The AMOUNT must match the "
            "quote. The DEADLINE must not pass before your payment confirms. "
            "The REFUND ADDRESS is where money returns if anything fails -- "
            "set it to something you control, it is the only safety net.\n\n"
            "Bridging out of ZEC, the deposit address is a t-address and "
            "bridge_send can quote and pay it in one step. Bridging in, you "
            "pay from your wallet on the origin chain. Ask about private "
            "bridging if you want the funds to land shielded."),
        "ground": ["bridge_chains"],
        "actions": [
            {"label": "Which chains and assets", "fn": "bridge_chains"},
            {"label": "Price a swap", "fn": "bridge_quote",
             "args": {"to_asset": "ETH", "amount": 1,
                      "recipient": "<0x address>", "refund_to": "<t1 address>"}},
        ],
    },
]


# ── Matching ────────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    return " ".join(_learn._words(text))


def match_intent(question: str):
    """The best intent for a question, or None. Score = phrases matched."""
    q = " " + _norm(question) + " "
    best, best_score = None, 0
    for intent in INTENTS:
        # Every "all" group must be represented, or the group bonus is void --
        # but the intent's own distinctive phrases can still carry it, which is
        # how "why can't I send shielded" reaches the right intent without
        # containing the word "bridge".
        groups = intent.get("all", [])
        hits = [sum(1 for phrase in group if _norm(phrase) in q) for group in groups]
        score = sum(h + 2 for h in hits) if hits and all(hits) else 0
        score += sum(1 for phrase in intent.get("any", []) if _norm(phrase) in q)
        if score > best_score:
            best, best_score = intent, score
    return (best, best_score) if best else (None, 0)


# Words that appear in every question and carry no signal. Without this, "what
# is the weather in paris" scores against every lesson through "what/is/the".
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "is", "are", "was", "were",
    "be", "been", "being", "am", "do", "does", "did", "doing", "have", "has",
    "had", "i", "me", "my", "mine", "you", "your", "it", "its", "this", "that",
    "these", "those", "there", "here", "what", "whats", "which", "who", "whom",
    "when", "where", "why", "how", "can", "cant", "could", "should", "would",
    "will", "shall", "may", "might", "must", "to", "of", "in", "on", "at", "by",
    "for", "with", "from", "about", "into", "as", "so", "than", "then", "too",
    "very", "just", "get", "got", "want", "need", "please", "tell", "know",
    "s", "t", "m", "re", "ve", "ll", "d", "not", "no", "yes", "any", "some",
    "one", "two", "up", "down", "out", "over", "again", "still", "now",
}


def retrieve(question: str, limit: int = 3) -> list:
    """Lessons ranked by word overlap with the question."""
    words = {w for w in _learn._words(question)
             if len(w) > 2 and w not in STOPWORDS}
    if not words:
        return []
    scored = []
    for lesson in _learn.LESSONS:
        hay = _learn._words(" ".join(
            [lesson["title"], lesson["summary"], " ".join(lesson["terms"])] +
            lesson["body"]))
        counts = {}
        for w in hay:
            counts[w] = counts.get(w, 0) + 1
        # Title and summary words are worth more than a passing mention.
        strong = set(_learn._words(lesson["title"] + " " + lesson["summary"] +
                                   " " + " ".join(lesson["terms"])))
        score = sum(min(counts.get(w, 0), 3) + (4 if w in strong else 0)
                    for w in words)
        # A question is "about" a lesson only if it shares real vocabulary with
        # it. One passing mention of a common word is noise, not a match.
        if score >= 4:
            scored.append((score, lesson))
    scored.sort(key=lambda s: (-s[0], s[1]["id"]))
    return [l for _, l in scored[:limit]]


def glossary_hits(question: str, limit: int = 4) -> list:
    """Glossary terms the question actually used."""
    q = " " + _norm(question) + " "
    hits, seen = [], set()
    candidates = sorted(
        list(_learn.GLOSSARY.keys()) + list(_learn.ALIASES.keys()),
        key=len, reverse=True)
    for term in candidates:
        if " " + _norm(term) + " " not in q:
            continue
        key = _learn.ALIASES.get(term, term)
        if key in seen or key not in _learn.GLOSSARY:
            continue
        seen.add(key)
        text, see = _learn.GLOSSARY[key]
        hits.append({"term": key, "definition": text, "lesson": see})
        if len(hits) >= limit:
            break
    return hits


# ── Answering ───────────────────────────────────────────────────────────────

def ask(question: str, mod=None, ground: bool = True, use_llm: bool = None) -> dict:
    """Answer a question about Zcash or about this module.

    `mod` is the Mod instance, used only to call read-only functions that make
    the answer concrete. Nothing that spends or reveals a secret is ever
    called -- the agent writes those calls out for you to run yourself.
    """
    q = (question or "").strip()
    if not q:
        raise AgentError("ask what?")

    intent, score = match_intent(q)
    lessons = retrieve(q)
    terms = glossary_hits(q)

    # "What is a viewing key" is a definition question, and a definition is a
    # better answer than the lesson that happens to mention the word most. This
    # only wins when no intent matched strongly, so "how private is Zcash"
    # still gets the essay rather than the one-line gloss on "private".
    definition = _definition_answer(q, terms)
    if definition and score < 3:
        lead = terms[0]
        ref = _learn.LESSON_INDEX.get(lead["lesson"])
        return {
            "question": q,
            "answer": definition,
            "confidence": "high",
            "intent": "define",
            "term": lead["term"],
            "lessons": ([{"id": ref["id"], "title": ref["title"],
                          "summary": ref["summary"]}] if ref else []),
            "terms": terms,
            "actions": _check_actions(
                list(ref.get("try", [])) if ref else [], mod),
            "grounded": {},
            "source": "glossary",
        }

    if intent:
        answer = intent["answer"]
        cited = [intent["lesson"]]
        actions = list(intent.get("actions", []))
        ground_fns = intent.get("ground", []) if ground else []
        confidence = "high" if score >= 3 else "medium"
    elif lessons:
        answer = _compose(q, lessons, terms)
        cited = [l["id"] for l in lessons]
        actions = [a for l in lessons[:2] for a in l.get("try", [])][:3]
        ground_fns = []
        confidence = "low"
    else:
        return {
            "question": q,
            "answer": ("I do not have a lesson covering that. This agent knows "
                       "Zcash itself -- addresses, shielded notes, sending, "
                       "fees, bridging, privacy and what this module can and "
                       "cannot do. It is not a general assistant and it does "
                       "not know prices of other assets, tax rules, or "
                       "anything about your other wallets.\n\n"
                       "Try naming a term: 'what is a viewing key', 'how do "
                       "fees work', 'can I bridge USDC into a shielded "
                       "address'."),
            "confidence": "none",
            "intent": None,
            "source": "none",
            "lessons": [{"id": l["id"], "title": l["title"]}
                        for l in _learn.LESSONS if l["level"] == "start"],
            "actions": [{"label": "Start from the beginning", "fn": "learn",
                         "args": {"path": "beginner"}}],
            "grounded": {},
            "terms": [],
        }

    grounded, errors = _ground(mod, ground_fns)

    out = {
        "question": q,
        "answer": answer,
        "confidence": confidence,
        "intent": intent["id"] if intent else None,
        "lessons": [{"id": i, "title": _learn.LESSON_INDEX[i]["title"],
                     "summary": _learn.LESSON_INDEX[i]["summary"]}
                    for i in dict.fromkeys(cited) if i in _learn.LESSON_INDEX],
        "terms": terms,
        "actions": _check_actions(actions, mod),
        "grounded": grounded,
        "source": "lessons",
    }
    if errors:
        out["grounding_errors"] = errors

    if use_llm is None:
        use_llm = bool(os.environ.get(_LLM_URL))
    if use_llm:
        try:
            out["answer"] = _llm_answer(q, out)
            out["source"] = "lessons+model"
            out["model"] = os.environ.get(_LLM_MODEL, "default")
        except AgentError as e:
            # The written answer is still correct; say the model was skipped
            # rather than failing the whole call.
            out["model_error"] = str(e)
    return out


# Phrasings that mean "define this for me" rather than "help me do something".
_DEFINITION_OPENERS = (
    "what is", "whats", "what s", "what does", "what are", "define",
    "meaning of", "what do you mean by", "explain the term", "tell me what",
)


def _definition_answer(question: str, terms: list) -> str:
    """A glossary answer, when the question is asking for one."""
    if not terms:
        return ""
    q = _norm(question)
    if not any(q.startswith(opener) or f" {opener} " in f" {q} "
               for opener in _DEFINITION_OPENERS):
        return ""
    lead = terms[0]
    parts = [f"{lead['term']} — {lead['definition']}"]
    ref = _learn.LESSON_INDEX.get(lead["lesson"])
    if ref:
        parts += ["", f"In context: “{ref['title']}”. {ref['summary']} "
                      f"Read it with learn(topic=\"{ref['id']}\")."]
    if len(terms) > 1:
        parts += ["", "You also mentioned: " + "; ".join(
            f"{t['term']} — {t['definition']}" for t in terms[1:3])]
    return "\n".join(parts)


def _compose(question: str, lessons: list, terms: list) -> str:
    """A written answer for a question no intent claimed."""
    lead = lessons[0]
    parts = [
        f"Closest match: “{lead['title']}”. {lead['summary']}",
        "",
        lead["body"][0],
    ]
    if len(lead["body"]) > 1:
        parts += ["", lead["body"][1]]
    if terms:
        parts += ["", "Terms you used: " + "; ".join(
            f"{t['term']} — {t['definition']}" for t in terms[:2])]
    if len(lessons) > 1:
        parts += ["", "Also relevant: " + ", ".join(
            f"“{l['title']}”" for l in lessons[1:])]
    parts += ["", "Open the full lesson with learn(topic=\"%s\")." % lead["id"]]
    return "\n".join(parts)


def _ground(mod, fns: list) -> tuple:
    """Call the read-only functions an intent asked for."""
    out, errors = {}, {}
    if not mod:
        return out, errors
    for fn in fns:
        if fn not in GROUNDING_FNS:
            errors[fn] = "not a grounding function"
            continue
        try:
            result = getattr(mod, fn)()
        except Exception as e:                      # a live read, any failure
            errors[fn] = f"{type(e).__name__}: {e}"
            continue
        if isinstance(result, dict) and result.get("error"):
            errors[fn] = result["error"]
            continue
        out[fn] = _trim(result)
    return out, errors


def _trim(value, depth: int = 0):
    """Keep grounding results small enough to read in an answer."""
    if isinstance(value, dict):
        if depth >= 2:
            return f"<{len(value)} fields>"
        return {k: _trim(v, depth + 1) for k, v in list(value.items())[:14]}
    if isinstance(value, list):
        if depth >= 2:
            return f"<{len(value)} items>"
        return [_trim(v, depth + 1) for v in value[:6]]
    if isinstance(value, str) and len(value) > 400:
        return value[:400] + "…"
    return value


def _check_actions(actions: list, mod) -> list:
    """Drop any suggested call whose function does not exist on this module.

    The agent's prose is hand-written, but its buttons are executable, and a
    button naming a function that was renamed is worse than no button.
    """
    if mod is None:
        return actions
    known = {n for n in dir(mod) if not n.startswith("_")}
    out = []
    for action in actions:
        fn = action.get("fn")
        if fn and fn not in known:
            continue
        out.append(action)
    return out


# ── Optional model ──────────────────────────────────────────────────────────

SYSTEM = """You answer questions about Zcash for someone who may be a complete \
beginner, inside a module that can explore the chain, hold a wallet, read \
shielded notes and bridge to other chains.

Rules, in order of importance:
1. Use ONLY the supplied context. If it does not answer the question, say so.
2. Never claim this module can spend shielded ZEC or create a shielded output \
without a proving node. It cannot; saying otherwise costs someone money.
3. Money-moving functions are dry runs unless broadcast=true. Say so whenever \
you describe one.
4. Plain language. No jargon without a one-line definition. Short paragraphs.
5. Do not invent function names, addresses or amounts."""


def _llm_answer(question: str, draft: dict) -> str:
    url = os.environ.get(_LLM_URL)
    if not url:
        raise AgentError("no model configured (set ZCASH_LLM_URL)")
    context = {
        "written_answer": draft["answer"],
        "lessons": [
            {"title": _learn.LESSON_INDEX[l["id"]]["title"],
             "body": _learn.LESSON_INDEX[l["id"]]["body"]}
            for l in draft["lessons"] if l["id"] in _learn.LESSON_INDEX],
        "glossary": draft["terms"],
        "live_data": draft["grounded"],
    }
    body = json.dumps({
        "model": os.environ.get(_LLM_MODEL, "gpt-4o-mini"),
        "temperature": 0.2,
        "max_tokens": 700,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content":
                f"Question: {question}\n\nContext:\n"
                f"{json.dumps(context, indent=1)[:14000]}"},
        ],
    }).encode()
    headers = {"Content-Type": "application/json"}
    key = os.environ.get(_LLM_KEY)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, ValueError, OSError) as e:
        raise AgentError(f"model call failed: {e}")
    try:
        text = payload["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        raise AgentError(f"unexpected model response: {str(payload)[:200]}")
    if not text:
        raise AgentError("model returned nothing")
    return text


def status() -> dict:
    """What the agent is backed by right now."""
    return {
        "intents": len(INTENTS),
        "lessons": len(_learn.LESSONS),
        "glossary_terms": len(_learn.GLOSSARY),
        "grounding_functions": sorted(GROUNDING_FNS),
        "model": {
            "configured": bool(os.environ.get(_LLM_URL)),
            "url": os.environ.get(_LLM_URL),
            "model": os.environ.get(_LLM_MODEL),
            "note": "Optional. Without it the agent answers from written "
                    "lessons, which is the default and works offline. Set "
                    "ZCASH_LLM_URL to any OpenAI-compatible "
                    "/chat/completions endpoint (plus ZCASH_LLM_KEY) to have "
                    "a model write the prose over the same sources.",
        },
        "limits": "Answers about Zcash and this module only. It never calls a "
                  "function that spends, deletes or reveals a secret -- it "
                  "writes that call out for you to run.",
    }
