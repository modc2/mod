# Module System

*This page explains modules — the building blocks that everything in mod is made of — and how you find, use, and create them.*

## What is a module?

A module is a self-contained piece of software that does one job well. One module talks to AI. Another stores files. Another handles payments. Everything in mod, without exception, is a module.

Think of them as LEGO bricks. Each brick is simple on its own, every brick snaps onto every other brick, and interesting things happen when you combine them. You never need to know how a brick was molded to click it into place.

## How do you use one?

You call a module by name and ask it to do something. That's it. Whether you're typing a quick command or writing a program, the pattern is the same: name the module, name the action, pass along any details.

Every module has one default action — its "front door" — so if you just call the module with nothing else, something sensible happens. Extra actions are there when you want them.

## Where do modules live?

Modules are organized into neighborhoods called orbits:

- **Core** — the framework's own essentials: keys, storage, the server. The load-bearing walls.
- **Orbit** — the main ecosystem, home to 200+ community modules.
- **Mods** — modules you've installed from the public registry.
- **Local** — modules sitting right in your current project folder.

When you ask for a module by name, mod checks each neighborhood in that order and hands you the first match. You never need to say where something lives — just its name.

## Finding what you need

You can list every module, search by name (even fuzzy search — a typo like "agnt" still finds "agent"), or ask any module to describe itself: what actions it offers, what each one expects, even its full source code. Every module is an open book.

## Making your own

Here's the best part: creating a module takes one folder and one file.

Inside the file, you write a small class — a labeled box of related actions — with the things you want it to do. Save it, and it's live. No sign-up, no configuration, no approval process. Mod discovers it automatically, the way a new book on your shelf is instantly part of your library.

The file you create is called the anchor file — it's the front door mod knocks on when it loads your module.

## Sharing it with the world

Any module can be turned into a live web service with a single command. Mod wraps it in a server so that every action becomes a web address other people (and other programs) can call over the internet.

If your module comes with its own visual interface — buttons and screens, not just raw responses — mod can serve that too, right alongside the service.

## Why this matters

Most software is a locked appliance: you get what you're given. Mod is a workshop. Every capability is a small, swappable, inspectable part — and adding your own part is as easy as dropping a folder in place.

*Want the details? Flip to Engineer mode.*
