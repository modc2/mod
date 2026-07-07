# Orbit Modules

*This page is a tour of the orbit ecosystem — the 200+ ready-made modules you can use today — and how to publish your own.*

## What is orbit?

Orbit is mod's marketplace of parts, except everything is free and open. It holds more than 200 modules, each one a ready-made capability you can pick up and use. Drop a new one into the folder and it's live — no sign-up, no approval, no waiting.

Think of it as a well-stocked toolshed. You don't build a hammer every time you need to hang a picture; you reach for the one on the wall.

## What's on the shelves?

A few of the aisles:

- **AI and agents** — assistants that can plan, use tools, remember past work, and write or fix code for you.
- **Storage** — modules that save your files to IPFS (a shared public hard drive that no one owns) and its cousins, so your data doesn't depend on any single company.
- **Money and markets** — trading tools, copy-trading engines, and bridges between different blockchain worlds.
- **Developer helpers** — modules for git, testing, remote machines, and more.

You can browse the whole collection with a single command, and every module will happily describe itself.

## A few standouts

- **agent** runs multi-step AI work: give it a goal, and it plans, acts, checks its results, and keeps going until done — like a diligent intern who writes down everything.
- **claude** puts an AI coding assistant on call: explain, generate, refactor, or debug code, and hand off long tasks to run in the background.
- **ipfs** handles public storage and even babysits its own connection — installing, starting, and healing itself without you noticing.
- **uniswap** trades on your behalf with strategies you set: buy a little every day, wait for a target price, or mirror a trader you trust.

## Making your own module

One folder, one small file, and your module is alive on your machine. That part takes a minute, literally. (The Module System page walks through it.)

## Sharing it with the world

When you're ready for other people to use your module, you publish it:

1. **Bundle it.** Your module's files are packed up and given a fingerprint — a CID, a code that uniquely identifies exactly these files. Change one letter and the fingerprint changes, so no one can quietly swap the contents.
2. **Register the name.** Your module's name and its fingerprint are written to a public registry on the blockchain — like recording a deed at city hall. From then on, anyone running mod can fetch your module just by its name, and the record proves it's yours.

## Keeping secrets safe

Some modules need private keys — say, a paid AI account. Those never go inside the published bundle. Instead, each user supplies their own, and it's locked with encryption only their personal wallet can open. The secret never travels anywhere readable, and no one — not even the server — can peek.

There's a second lock too: only a module's owner can change what's published. Anyone else who tries simply gets a polite "no."

## One address for everything

Every published module lives behind one clean web address: the module's name for its app, and a matching address for its service. The routing is generated automatically from each module's settings — nobody hand-edits a traffic map. Publish, opt in, and the front door appears.

## Why this matters

Orbit turns "I wish there were a tool for this" into "there probably already is — and if not, I can make one this afternoon and share it with everyone." That's the whole idea: a commons of capabilities, owned by the people who build them.

*Want the details? Flip to Engineer mode.*
