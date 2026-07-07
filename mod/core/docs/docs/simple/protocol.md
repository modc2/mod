# The Mod Protocol

*This page explains the handful of shared rules that turn any folder of code into a Mod module.*

## What is a protocol, anyway?

A protocol is just an agreement — a set of rules everyone follows so things fit together. Electrical outlets are a protocol: because every plug and socket match, any appliance works in any home. The mod protocol does the same for software. Follow a few small rules, and your code plugs into everything else.

## Rule 1: A module is a folder

That's the whole rule. A module is a folder with one main file inside that says what the module can do. Each ability is just a plain function — no ceremony, no sign-up step.

Put that folder in the right place, and Mod finds it. You can immediately run it from Python or from the command line, exactly like every other module.

## Rule 2: Any module can become a website

One command turns a module into a live web service. Every ability the module has becomes a web address you can send requests to.

There's a lovely trick built in: if you knock on a module's front door without asking for anything specific, it introduces itself — its name, its abilities, and how to call them. So you never need a manual to figure out what a module can do. You just ask it.

## Rule 3: One address scheme for everything

Every Mod deployment arranges its web addresses the same way:

- **The app** (the part people see, with buttons and pages) lives at `/{module}` — for example, `/store`.
- **The API** (the part programs talk to) lives at `/api/{module}`.

It's like a city where every street follows the same grid. Once you know one module's address, you know them all. Several different pieces of software can play traffic director, but they all enforce this same rule — so the map never changes.

## Rule 4: Keys prove who you are

Instead of usernames and passwords, Mod uses cryptographic keys — a digital signature only you can produce, but anyone can check. A module can name an owner, and certain sensitive abilities will only respond when the request is genuinely signed by that owner.

One quiet but important habit: private things — guest lists, secrets, who's allowed in — live only on the owner's machine, never in the public code.

## Rule 5: Data can live in three places

- **On your machine** — everyday saves, optionally locked with encryption.
- **On IPFS** — a shared public hard drive that no one owns. Anything stored there gets a unique fingerprint (a CID), so anyone can fetch exactly that content, byte for byte.
- **On the blockchain** — a public ledger records which name belongs to which owner. That makes a module's existence provable, and its ownership transferable, without trusting any middleman.

## The whole protocol in one breath

A folder, a class, one address rule, and a knock on the door that makes any module introduce itself. Everything else in Mod is built on top of those few agreements.

*Want the details? Flip to Engineer mode.*
