# Mod Framework Documentation

*This page is the front door: what Mod is, what it's made of, and where to go next.*

## What is Mod?

Mod is a toolkit for building apps out of small, reusable pieces called **modules**. Think of it like LEGO for software: every piece snaps together the same way, so you can build something big without gluing anything by hand.

Each module is just a folder of code. Once it follows a couple of simple rules, you can run it from your computer, turn it into a website, share it with the world, or even prove you own it — all without changing the module itself.

## What's in the box?

Mod comes with a few core parts that everything else builds on:

- **A command line.** One short command, `m`, lets you list modules, run them, and manage them — like a universal remote for your whole system.
- **Modules.** Over 200 ready-made pieces: AI helpers, file storage, trading tools, and more. You can use them as-is or build your own.
- **Keys.** Your digital signature. A key proves "this is really me" without a username or password, and it works across different blockchains.
- **Storage.** A simple place to save things on your own machine, with optional encryption (locking your data with a password). For sharing, Mod uses IPFS — a shared public hard drive that no one owns.
- **Servers.** Any module can become a live web service with one command. No extra setup.
- **Smart contracts.** Small programs that live on a public blockchain (Base) and handle money, ownership, and access — like a vending machine that no one can tamper with.

## The big idea: everything is a module

In most software, every project has its own shape and its own quirks. In Mod, everything — the AI agent, the file store, even these docs — is the same kind of thing: a module.

That one decision does a lot of work. Because every module looks the same, one command line can run them all, one server can host them all, and one registry can keep track of them all.

## Where does everything live?

Modules are grouped into neighborhoods called **orbits**:

- **core** — the framework's own essentials, the parts everything depends on.
- **orbit** — the big open ecosystem, where most of the 200+ modules live.
- **local** and **mods** — your own modules and ones you've installed.

When you ask for a module by name, Mod checks every neighborhood until it finds it.

## Why this matters

Mod is built so that anyone can create a piece of software, share it, prove they made it, and get paid for it — without asking a company for permission. The modules are open, the storage is public, and the ownership records live on a blockchain anyone can check.

## Where to next?

- **Getting Started** — install Mod and run your first commands.
- **Protocol** — the handful of rules that make everything fit together.
- **CLI Reference** — get comfortable with the `m` command.

*Want the details? Flip to Engineer mode.*
