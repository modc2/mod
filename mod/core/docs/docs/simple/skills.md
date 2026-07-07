# Skills Reference

*This page is a tour of everything mod can do for you, grouped by what you're trying to get done.*

## What is a skill?

A skill is just something mod knows how to do when you ask. Think of mod as a Swiss Army knife: one handle, dozens of tools folded inside. You can ask for any of them the same two ways — by typing a short command in your terminal, or from inside a Python program. Same knife, two grips.

The rest of this page walks through the big blades.

## Working with modules

Modules are the building blocks of everything here — small, self-contained apps that anyone can make and share. mod helps you browse the whole collection, peek inside any module to see what it does, and pull one apart to read its actual source. When you're ready to build, you can create a fresh module of your own, copy an existing one as a starting point, or import one someone else published — even straight from a shared fingerprint of the code (called a CID, more on that below).

## Your keys and identity

mod gives you cryptographic keys — think of a key as a signature stamp that only you own and no one can forge. With it you can sign messages (prove "yes, this really came from me"), verify other people's signatures, and lock or unlock private data so only the right person can read it. Your keys work across several blockchain networks, so one identity travels with you.

## Saving things

You can stash any piece of data under a name and get it back later, like a labeled drawer. Add a password and the drawer locks itself. For sharing beyond your own machine, mod plugs into IPFS — a shared public hard drive that no one owns. Anything you put there gets a permanent fingerprint (a CID), and anyone holding that fingerprint can fetch the exact same content, forever.

## Turning modules into live services

Any module can be put on the air as a web service with one command — suddenly other people (or other programs) can call it over the internet. mod also handles the housekeeping: starting services, stopping them, listing what's running, and reading their logs when something looks off. It's like having a stage manager who can raise the curtain on any act and keep an eye on the whole show.

## Asking AI for help

mod has AI built in. You can ask it plain questions, ask it to explain what a module does, or ask how to use one. It can even edit code for you — describe the change you want and it makes it. There's also a full agent mode: hand it a bigger goal, like "build me a small web service," and it works through the steps on its own.

## Everyday chores

The rest of the toolkit covers the small stuff you'd otherwise do by hand: browsing files and folders, saving your work to a code repository and sharing it, checking what time it is, finding a free network port, running tests, and opening a module in your editor. Not glamorous — just always there when you reach for it.

## The bigger connected pieces

A few modules deserve a special mention because they connect mod to the wider world:

- **IPFS** — the shared public hard drive mentioned above. Store something once, and it lives at a permanent address anyone can use.
- **Agent** — an autonomous AI worker. Give it a goal and a toolbox, and it takes multiple steps on its own to get there.
- **Claude** — a direct line to the Claude AI for code work: explaining, generating, fixing, and refactoring, including long jobs that run in the background.
- **Uniswap** — automated trading strategies on a crypto exchange, like scheduled buying or following a trader you trust.
- **BlocTime** — mod's own on-chain layer: buying credits, registering your modules on a public ledger, and building blockchain transactions.
- **Bridge** — a ferry for tokens, carrying value from one blockchain network across to another.

## Why this matters

You don't have to remember any of this all at once. The point is simply: whatever you're trying to do — build, share, save, sign, serve, or ask — there's probably one short command for it. Reach for the knife and the right blade is there.

*Want the details? Flip to Engineer mode.*
