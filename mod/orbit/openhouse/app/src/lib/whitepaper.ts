// OpenHouse — the whitepaper. Rent-to-own, on-chain.
// Plain language. Read it on the train and get it in one stop.

export const MANIFESTO = [
  "Rent is a tax on being broke.",
  "Platforms take 15% off the top for holding the money.",
  "OpenHouse takes 0–5% — and every check buys you the house.",
]

export const ABSTRACT =
  "OpenHouse is rent-to-own, on-chain. You rent a home like normal — but the " +
  "protocol keeps 0–5% (the owner picks the number; the cap is written into the " +
  "contract, not a promise) and 95–100% of every payment stays with the property. " +
  "The owner chooses a rent-to-own model that decides how that money splits between " +
  "your equity and their income — from a classic 25% rent credit to every net dollar " +
  "buying the house. Each quarter ownership is redistributed by principal paid off. " +
  "Pay it off, own it outright.\n\n" +
  "A financed home works the same way with more people in it. Any group can form a " +
  "trust against one mortgage: each member holds a share token minted one-for-one " +
  "against dollars the servicer confirms it posted, so equity is pro-rata to what " +
  "everyone actually paid in. An oracle run by the lender carries the bill and the " +
  "confirmation, and the lender holds every lever on the contract — because it fronted " +
  "the principal and carries the loss. Control and liability are the same seat."

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
  /** its own page at /openhouse/paper/<slug> — permalinks, so don't rename one
   *  after it's been shared. The home page renders the same list inline. */
  slug: string
  kicker: string
  title: string
  body: string[]
  pull?: string
}

export const SECTIONS: PaperSection[] = [
  {
    no: "01",
    slug: "the-problem",
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
    slug: "rent-to-own",
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
    slug: "the-take",
    kicker: "The Take",
    title: "One to five percent. Written into the contract.",
    body: [
      "Airbnb clears roughly 15% of what a guest pays. Vrbo lands near 13%. A property " +
        "manager takes 8–12% of the rent and calls it a service. None of it buys the person " +
        "paying so much as a doorknob.",
      "OpenHouse takes 0–5%. Not as a pledge on a pricing page — MIN_FEE_BPS and MAX_FEE_BPS " +
        "are constants in the contract, so no future version of us can widen the band without " +
        "deploying a different contract in front of everybody. Inside it, the property's owner " +
        "sets the number. Their building, their call.",
      "The other 95–100% never leaves the property. It splits between the renter's equity and " +
        "the owner's income by whichever rent-to-own model the owner picked — and both halves " +
        "are visible on-chain, per payment, forever.",
    ],
    pull: "A platform should cost what a wire transfer costs — not a fifth of somebody's home.",
  },
  {
    no: "04",
    slug: "the-models",
    kicker: "The Models",
    title: "The owner sets the dial.",
    body: [
      "Rent-to-own isn't one contract, it's a family of them, and OpenHouse ships the family. " +
        "Full credit: every post-fee dollar becomes principal, and the owner earns from lowfi " +
        "yield instead of rent. Hybrid: half equity, half income — the honest deal when the owner " +
        "still carries a mortgage. Classic lease-option: a 25% rent credit plus an upfront option " +
        "fee, the shape the industry already uses. Plain lease: no equity, but the owner still " +
        "keeps 95–100% instead of handing a platform double digits.",
      "The owner picks a model, then tunes it — credit percentage, option fee, monthly payment. " +
        "The renter sees the exact split before paying, because the same function that moves the " +
        "money will quote it first.",
    ],
    pull: "Four models, one dial, zero fine print. The number you're shown is the number that executes.",
  },
  {
    no: "05",
    slug: "redistribution",
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
    slug: "the-yield",
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
  {
    no: "07",
    slug: "the-trust",
    kicker: "The Trust",
    title: "Any set of individuals, one house, one mortgage.",
    body: [
      "One person rarely clears a down payment on a whole house. Several people usually can. " +
        "A trust is what lets them: any group can file a formation — the house, the terms, " +
        "the list of who is in — and every one of them pays into the same mortgage.",
      "Each member holds a share token. The token is not a promise or a claim to be argued " +
        "about later; it is a receipt for dollars that reached the loan. Shares are minted at " +
        "one share per dollar, so your fraction of the house is your balance over the total " +
        "supply. Put in 60% of the money and you own 60% of the house. There is no other rule, " +
        "and no function in the contract that can change the ratio — the only way your share " +
        "moves is somebody paying more, or you paying less.",
      "Somebody joining in year three doesn't take anything from you. They dilute everyone by " +
        "exactly the dollars they bring and not a basis point more, because the arithmetic is " +
        "the same arithmetic it was on day one.",
    ],
    pull: "Your equity is your dollars over everyone's dollars. That is the whole model.",
  },
  {
    no: "08",
    slug: "the-oracle",
    kicker: "The Oracle",
    title: "A payment isn't a payment until the bank says it posted.",
    body: [
      "A mortgage lives in a servicing system, not on a blockchain. So the trust doesn't guess. " +
        "The lender runs an oracle — a feed its own reporters sign — and it carries two things: " +
        "this month's bill, and confirmation that the servicer actually posted the money.",
      "Money you send is escrow, not equity. It sits in the contract, goes out in one wire to " +
        "one address the bank named, and becomes shares only when the feed confirms the loan was " +
        "credited. Reporters have to agree byte for byte before anything publishes; a stale feed " +
        "stops the trust taking money at all, which makes oracle liveness the bank's problem " +
        "rather than yours.",
      "The feed cuts both ways. It is also what the bank has to point at before it can call the " +
        "loan: default is gated on the oracle saying the period went unpaid, not on the lender's " +
        "say-so. And when the balance reaches zero, anyone at all can discharge the mortgage on " +
        "chain — the bank cannot decline to admit it was repaid.",
      "It also settles the oldest argument in rent-to-own: what does a payment buy? Set the trust " +
        "to the Principal basis and only the amortized principal in a payment mints equity. The " +
        "interest is what the bank's capital costs, and it is priced as a cost, in public, every " +
        "month.",
    ],
    pull: "Escrow until the servicer posts it. Then it is yours, and the chain says so.",
  },
  {
    no: "09",
    slug: "the-bank",
    kicker: "The Bank",
    title: "Whoever carries the risk holds the keys.",
    body: [
      "The bank fronted the principal. It carries the loan, and if the house burns down or the " +
        "group walks away it eats the loss. So it controls this contract — not a DAO, not a " +
        "multisig of enthusiasts, not us. It appoints the oracle, admits the members, names the " +
        "account payments are wired to, can freeze everything on its own signature, can call the " +
        "default and can foreclose. There is no timelock and no member vote standing between a " +
        "lender and its collateral, because a lender that can't reach its collateral doesn't lend.",
      "That is the trade, stated plainly: total control is what buys total liability. A protocol " +
        "that spread the keys around would also have spread the losses around, onto people who " +
        "never underwrote anything.",
      "What the bank cannot do is take your stake — not because it promised, but because the " +
        "functions were never written. There is no burn. There is no seize. Escrowed money leaves " +
        "by exactly two doors, to the servicer or back to you. Freezing a member stops them acting " +
        "and touches nothing they own; they keep every share and can still collect income already " +
        "earned. The one power over shares is all-or-nothing relocation to another cleared address " +
        "— a lost key, a death, a court order — and it cannot shrink a position or invent one.",
      "When the bank covers a shortfall to keep the loan current, that is a loan to the trust and " +
        "not a purchase of it. It ranks ahead of every distribution, accrues at a capped rate, and " +
        "is repaid in cash. It mints the bank nothing. You are never diluted by the rescue.",
    ],
    pull: "Total control is what buys total liability. Spread the keys and you spread the losses.",
  },
  {
    no: "10",
    slug: "the-city",
    kicker: "The City",
    title: "Any government can hold a seat here.",
    body: [
      "Housing is the most regulated market on earth, and the regulator has never been able to " +
        "see it. A city learns a landlord cooked the books from an eviction filing, years late. " +
        "OpenHouse inverts that: every term and every split of every payment is public, and a " +
        "government that wants more than visibility can take a seat in the contract itself.",
      "The civic seat works like everything else here — powers that exist are written as " +
        "functions, powers that don't were never written. A chartered authority runs its own " +
        "verification servers, on its own infrastructure, re-deriving every split in a " +
        "property's ledger from scratch: the fee inside the band, the equity clamp, the totals. " +
        "It doesn't audit our arithmetic by trusting our arithmetic. And what it verifies it " +
        "can enforce: a civic pause that freezes payments and the bank cannot clear, and a " +
        "foreclosure hold — notice, thirty days in the open, and no taking while the hold is " +
        "up, for as long as the city keeps it up.",
      "The charter cuts both ways, plainly. Once the owner seats an authority it cannot unseat " +
        "it — only the government can resign, and its flags lift when it leaves. And the " +
        "authority cannot touch a balance, mint a share, or move a cent: a civic pause protects " +
        "people from the deal, never the deal from its people. A city that registers in " +
        "CivicRegistry publishes its key on its own .gov domain, so 'adopted by the city' is a " +
        "claim anyone can check both halves of.",
      "And a city that wants the whole program doesn't need our permission or a special mode. " +
        "The lender in this protocol is an address. A housing authority takes the bank seat, " +
        "runs its own oracle reporters, charters itself — or its state — as authority, sets the " +
        "fee to zero, and operates city-owned rent-to-own in public, supervised by its own " +
        "servers, auditable by anyone. Every city can run this. That's the point of shipping it " +
        "as open source instead of as a company.",
    ],
    pull: "Verification from the city's own servers. Override written into the contract. " +
      "City-owned rent-to-own is the city taking the bank seat.",
  },
]

/** One section plus the two either side of it — everything a section page needs. */
export function paperSection(slug: string) {
  const i = SECTIONS.findIndex(s => s.slug === slug)
  if (i === -1) return null
  return { section: SECTIONS[i], prev: SECTIONS[i - 1] ?? null, next: SECTIONS[i + 1] ?? null }
}

export const TOKENOMICS = [
  { label: "Protocol take", value: "0–5%", note: "owner-set, capped in code" },
  { label: "Stays with the home", value: "95–100%", note: "equity + owner income" },
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
  { name: "OpenHouse", take: 3, note: "owner-set, 0–5% hard cap", ours: true },
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
  "THEY TAKE 15% · WE TAKE 0–5%",
  "95–100% STAYS WITH THE HOME",
  "EVERY CHECK BUYS THE HOUSE",
  "PRINCIPAL = OWNERSHIP",
  "REDISTRIBUTED QUARTERLY",
  "RENTERS EARN WALLS",
  "OWNERS EARN YIELD",
  "PAY IT OFF, OWN IT",
  "NO MORE RECEIPTS",
]
