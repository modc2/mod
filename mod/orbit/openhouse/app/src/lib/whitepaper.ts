// OpenHouse — the whitepaper. Rent-to-own, on-chain.
// Plain language. Read it on the train and get it in one stop.

export const MANIFESTO = [
  "Rent is a tax on being broke.",
  "Platforms take 15% off the top for holding the money.",
  "OpenHouse takes 1–5% — and every check buys you the house.",
]

export const ABSTRACT =
  "OpenHouse is rent-to-own, on-chain. You rent a home like normal — but the " +
  "protocol keeps 1–5% (the owner picks the number; the cap is written into the " +
  "contract, not a promise) and 95–99% of every payment stays with the property. " +
  "The owner chooses a rent-to-own model that decides how that money splits between " +
  "your equity and their income — from a classic 25% rent credit to every net dollar " +
  "buying the house. Each quarter ownership is redistributed by principal paid off. " +
  "Pay it off, own it outright."

// Where the protocol actually stands today. Every "is this live?" string on the
// site reads from here — flip it in one place the day mainnet ships.
export const LAUNCH = {
  stage: "Testnet",
  chain: "Base Sepolia",
  chainId: "84532",
  date: "To be announced",
  short: "Testnet · mainnet launch TBA",
  notice:
    "OpenHouse is running on Base Sepolia testnet. Test ETH only — no real money, " +
    "no real rent, no real deed. Mainnet launch date to be announced.",
}

export interface PaperSection {
  no: string
  kicker: string
  title: string
  body: string[]
  pull?: string
}

export const SECTIONS: PaperSection[] = [
  {
    no: "01",
    kicker: "The Problem",
    title: "Rent is extraction.",
    body: [
      "Every month a chunk of your income disappears into someone else's asset. " +
        "You get a roof for 30 days. They get the equity, the appreciation, and the tax write-off.",
      "Pay rent on time for a decade and you own exactly zero of the place you live. " +
        "That's not a market — it's a meter running on your life. By design, rent builds " +
        "nothing for the person paying it and transfers wealth upward, forever.",
    ],
    pull: "You can rent for 30 years and end up with nothing but receipts.",
  },
  {
    no: "02",
    kicker: "The Model",
    title: "Rent that turns into ownership.",
    body: [
      "OpenHouse is rent-to-own. You move in and pay monthly, like any tenant. The " +
        "difference: your payment is recorded on-chain as principal toward the home — not a landlord's profit.",
      "Every dollar of principal is a brick. The more you pay, the more of the house is yours. " +
        "When your principal reaches the home's price, you own it outright — title and all.",
    ],
    pull: "Same check. Opposite outcome. You're buying the home you live in, one month at a time.",
  },
  {
    no: "03",
    kicker: "The Take",
    title: "One to five percent. Written into the contract.",
    body: [
      "Airbnb clears roughly 15% of what a guest pays. Vrbo lands near 13%. A property " +
        "manager takes 8–12% of the rent and calls it a service. None of it buys the person " +
        "paying so much as a doorknob.",
      "OpenHouse takes 1–5%. Not as a pledge on a pricing page — MIN_FEE_BPS and MAX_FEE_BPS " +
        "are constants in the contract, so no future version of us can widen the band without " +
        "deploying a different contract in front of everybody. Inside it, the property's owner " +
        "sets the number. Their building, their call.",
      "The other 95–99% never leaves the property. It splits between the renter's equity and " +
        "the owner's income by whichever rent-to-own model the owner picked — and both halves " +
        "are visible on-chain, per payment, forever.",
    ],
    pull: "A platform should cost what a wire transfer costs — not a fifth of somebody's home.",
  },
  {
    no: "04",
    kicker: "The Models",
    title: "The owner sets the dial.",
    body: [
      "Rent-to-own isn't one contract, it's a family of them, and OpenHouse ships the family. " +
        "Full credit: every post-fee dollar becomes principal, and the owner earns from lowfi " +
        "yield instead of rent. Hybrid: half equity, half income — the honest deal when the owner " +
        "still carries a mortgage. Classic lease-option: a 25% rent credit plus an upfront option " +
        "fee, the shape the industry already uses. Plain lease: no equity, but the owner still " +
        "keeps 95–99% instead of handing a platform double digits.",
      "The owner picks a model, then tunes it — credit percentage, option fee, monthly payment. " +
        "The renter sees the exact split before paying, because the same function that moves the " +
        "money will quote it first.",
    ],
    pull: "Four models, one dial, zero fine print. The number you're shown is the number that executes.",
  },
  {
    no: "05",
    kicker: "Redistribution",
    title: "Ownership, recomputed every quarter.",
    body: [
      "Four times a year the contract redistributes ownership based on principal paid off. " +
        "Your stake = your principal ÷ the home's price. Pay more, own more — automatically, " +
        "transparently, on a fixed quarterly cadence.",
      "No appraisals, no negotiation, no hidden math. The cap table updates itself from what " +
        "everyone has actually paid.",
    ],
    pull: "Your ownership isn't promised — it's measured, every quarter, from real principal.",
  },
  {
    no: "06",
    kicker: "The Yield",
    title: "The owner's money works too.",
    body: [
      "While you pay down the house, the current owner doesn't sit on the cash. Pooled payments " +
        "are routed into low-risk on-chain yield (lowfi), earning interest on funds in flight.",
      "That yield rewards the owner for fronting the asset and helps cover costs — so the deal " +
        "works for both sides. You build equity; they earn yield; nobody gets extracted.",
      "No subsidy, no mandate, no coercion. Just a voluntary market where capital is priced " +
        "fairly, put to work, and property ends up in the hands of the people actually paying for it. " +
        "The redistribution isn't taken — it's earned, one payment at a time.",
    ],
    pull: "Renters earn walls. Owners earn yield. Markets do the redistributing.",
  },
]

export const TOKENOMICS = [
  { label: "Protocol take", value: "1–5%", note: "owner-set, capped in code" },
  { label: "Stays with the home", value: "95–99%", note: "equity + owner income" },
  { label: "Your stake", value: "Principal ÷ price", note: "real equity, not points" },
  { label: "Redistribution", value: "Quarterly", note: "every 90 days, on-chain" },
  { label: "Paid in full", value: "100% = title", note: "own it outright" },
]

// What the incumbents skim off the top. Published headline rates — the argument
// is the order of magnitude, not the decimal. Mirrors Mod.BENCHMARKS in mod.py.
export const BENCHMARKS = [
  { name: "Airbnb", take: 15, note: "host 3% + guest ~14%" },
  { name: "Vrbo / Booking", take: 13, note: "commission + processing" },
  { name: "Property manager", take: 10, note: "8–12% of monthly rent" },
  { name: "OpenHouse", take: 3, note: "owner-set, 1–5% hard cap", ours: true },
]

export const ROADMAP = [
  { phase: "Now", title: "Testnet — Base Sepolia", done: true,
    detail: "Contract, API, and app are shipped and open source. Deploy a property with test ETH and run the whole loop: pay principal, track equity, redistribute quarterly. Nothing here is real money." },
  { phase: "Next", title: "Mainnet + first real home — launch date TBA", done: false,
    detail: "Legal wrapper, audited contract, and live lowfi yield routing on a genuine address. We announce a date once the audit clears — not before." },
  { phase: "Soon", title: "Multi-home portfolios", done: false,
    detail: "Rent across buildings on one equity ledger — move and keep stacking principal." },
  { phase: "Vision", title: "A city where renters become owners", done: false,
    detail: "Every lease, a path to a deed. The skyline, handed back to the people paying for it." },
]

export const TICKER = [
  "TESTNET · LAUNCH TBA",
  "RENT IS EXTRACTION",
  "THEY TAKE 15% · WE TAKE 1–5%",
  "95–99% STAYS WITH THE HOME",
  "EVERY CHECK BUYS THE HOUSE",
  "PRINCIPAL = OWNERSHIP",
  "REDISTRIBUTED QUARTERLY",
  "RENTERS EARN WALLS",
  "OWNERS EARN YIELD",
  "PAY IT OFF, OWN IT",
  "NO MORE RECEIPTS",
]
