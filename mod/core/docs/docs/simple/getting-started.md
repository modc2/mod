# Getting Started

*This page gets Mod onto your computer and walks you through your first few minutes with it.*

## What you'll need

Two free tools, which most developers' machines already have:

- **Python** — the language Mod is written in.
- **Node.js** — used for the web pages some modules show.

If you're missing either, a quick search for "install Python" or "install Node.js" will get you there in a few minutes. Everything else Mod needs, it can fetch for itself.

## Installing

Installing means copying Mod to your computer and telling Python about it. Two commands in your terminal:

```bash
git clone https://github.com/your-org/mod.git && cd mod
```
*Downloads Mod and steps into its folder.*

```bash
pip install -e .
```
*Installs it, and gives you the `m` command everywhere.*

That's it. From now on, typing `m` in any terminal talks to Mod.

## Your first command

```bash
m mods
```
*Shows every module you have — your full toolbox, listed by name.*

Try a few more: `m search agent` finds modules by keyword, and `m info agent` tells you what a module does and what it can be asked.

## The pattern behind everything

Every module is a folder full of abilities, and you call an ability like this: `m module/ability`, plus whatever details it needs. For example, `m agent/forward query="hello"` asks the agent module to run its `forward` ability with your question.

Learn that one pattern and you know how to use all 200+ modules. It's like learning one door handle and being able to open every door in the building.

## A few things you can do right away

- **Save and fetch data.** `m put` and `m get` store little notes on your machine — a personal filing cabinet. Add a password and Mod locks them with encryption.
- **Create a key.** `m get_key main` makes your digital identity: an address that can sign things, so anyone can verify "yes, this really came from me."
- **Launch a server.** `m serve` turns any module into a live web service, reachable from a browser. One command, no configuration.
- **Share on IPFS.** The `ipfs` module saves data to a shared public hard drive that no one owns, and hands back a fingerprint (a CID) anyone can use to fetch it.

## Where your stuff lives

Mod keeps everything it saves — keys, stored data, settings — in one hidden folder in your home directory, called `.mod`. It stays on your machine unless you deliberately share it.

## Next steps

- **Protocol** — the simple rules that make every module click together.
- **CLI Reference** — more of what the `m` command can do.

*Want the details? Flip to Engineer mode.*
