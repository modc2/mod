// OpenHouse — the whitepaper. Rent-to-own, on-chain.
// Plain language. Read it on the train and get it in one stop.

export const MANIFESTO = [
  "Rent is a tax on being broke.",
  "You pay a landlord's mortgage for years and walk away owning nothing.",
  "OpenHouse flips it — every rent check buys you the house.",
]

export const ABSTRACT =
  "OpenHouse is rent-to-own, on-chain. You rent a home like normal — but every " +
  "payment is recorded as principal toward the house, not profit for a landlord. " +
  "Each quarter, ownership is redistributed by how much principal you've paid off: " +
  "pay more, own more. Meanwhile the home's owner parks the pooled payments in " +
  "low-risk DeFi yield (lowfi) so the money earns interest while it sits. Pay it " +
  "off, own it outright."

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
    no: "04",
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
  { label: "Your stake", value: "Principal ÷ price", note: "real equity, not points" },
  { label: "Redistribution", value: "Quarterly", note: "every 90 days, on-chain" },
  { label: "Idle funds", value: "lowfi yield", note: "low-risk DeFi interest" },
  { label: "Paid in full", value: "100% = title", note: "own it outright" },
]

export const ROADMAP = [
  { phase: "Now", title: "Rent-to-own protocol live on Base Sepolia", done: true,
    detail: "Contract, API, and app shipped. Pay principal, track equity, redistribute quarterly." },
  { phase: "Next", title: "Mainnet + first real home", done: false,
    detail: "Legal wrapper, audited contract, and live lowfi yield routing on a genuine address." },
  { phase: "Soon", title: "Multi-home portfolios", done: false,
    detail: "Rent across buildings on one equity ledger — move and keep stacking principal." },
  { phase: "Vision", title: "A city where renters become owners", done: false,
    detail: "Every lease, a path to a deed. The skyline, handed back to the people paying for it." },
]

export const TICKER = [
  "RENT IS EXTRACTION",
  "EVERY CHECK BUYS THE HOUSE",
  "PRINCIPAL = OWNERSHIP",
  "REDISTRIBUTED QUARTERLY",
  "RENTERS EARN WALLS",
  "OWNERS EARN YIELD",
  "PAY IT OFF, OWN IT",
  "NO MORE RECEIPTS",
]
