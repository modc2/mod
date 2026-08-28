# CLI Reference

*This page explains the `m` command — the single remote control for everything in Mod.*

## One command to rule them all

Mod gives you one short command, `m` (with `c` as a twin that works identically). Whatever you want to do — run a module, save data, start a server — it starts with `m`.

Think of it as the front desk of a big hotel. You don't need to know which floor anything is on. You just tell the front desk what you want, and it connects you.

## The one sentence pattern

Every request follows the same shape: **which module, which ability, and any details**.

```bash
m agent/forward query="build something"
```
*Ask the `agent` module to run its `forward` ability, with your request as the detail.*

Read it aloud: "m, go to agent, run forward, here's my query." That's the entire grammar. Details can be given in order (like `m hash "hello"`) or by name (like `port=8000`) — whichever reads more naturally.

## It understands what you mean

You don't have to fuss over formats. Type a number and Mod treats it as a number. Type `true` or `false` and it becomes a yes or no. Type a list and it becomes a list. The command line quietly translates your plain typing into what the code expects.

And if you skip the ability name entirely — just `m agent` — the module runs its default action, like pressing the big main button on a machine.

## Things you'll actually type

A tour of the everyday moves, in plain words:

- **Finding things.** `m mods` lists every module you have. `m search` hunts by keyword. `m info` makes a module describe itself, and `m fns` lists everything it can do. `m code` even shows you its source, if you're curious how it works.
- **Saving things.** `m put` stores a note under a name; `m get` fetches it back. Add a password and it's locked with encryption — only that password opens it again.
- **Proving things.** `m get_key` creates your digital identity. `m sign` stamps a message with your signature, and `m verify` lets anyone confirm the stamp is genuine — like sealing wax, but math.
- **Running things.** `m serve` turns a module into a live web service. `m servers` shows what's running, and `m kill` stops one.
- **Housekeeping.** Save your work to Git, find a free network port, hash some data, check the time. Small chores, same front desk.

## What you see back

When a command finishes, the answer prints right in your terminal, in green, followed by how long it took. Long-running tasks stream their progress line by line, so you're never staring at a silent screen wondering if anything is happening.

## Why this matters

Most software makes you learn a new interface for every tool. Mod makes you learn one sentence — module, ability, details — and every one of its 200+ modules answers to it. The command line isn't a hurdle here; it's the shortcut.

*Want the details? Flip to Engineer mode.*
