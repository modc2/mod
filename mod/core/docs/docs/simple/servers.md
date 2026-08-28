# Server Management

*This page explains how a module goes from code sitting on your machine to a live service anyone can talk to.*

## What does "serving" a module mean?

A module is just a bundle of useful functions. Serving it means putting those functions online, so other programs — or other people — can use them over the internet instead of only on your computer.

Think of a food truck. The recipes (your module's functions) already exist. Serving is parking the truck, opening the window, and putting up a menu. Now anyone who walks up can order.

## How it works

When you serve a module, the framework does three things for you:

1. It reads your module and finds every public function — that becomes the menu.
2. It opens a window: each function gets its own web address where requests come in and answers go out.
3. It writes the new service into a shared directory, so everything else on the network knows it exists and where to find it.

You don't wire any of this up yourself. One command, and the truck is open for business.

## What callers see

Every function in your module becomes a simple address. Someone sends a request with their inputs, and gets your function's answer back. If they knock on the module's front door without naming a function, they get the menu itself — a description of everything the module can do. That way, nobody needs a manual to discover a service.

## API, app, or both?

A served module always exposes its functions (the API — the machine-to-machine side). If your module also comes with a web page — buttons and screens for humans — you can serve that too, right alongside. One switch decides whether you're opening just the kitchen window or the whole dining room.

## Who keeps the lights on?

Behind the scenes, a process manager (a piece of software whose whole job is babysitting other programs) watches every server. If one crashes, it restarts it. It collects logs, tracks how much memory and power each one uses, and can bring everything back up after a reboot. It's the night manager who never sleeps.

## Finding and managing your servers

At any moment you can ask: what's running, and where? The built-in registry answers with a list of live services and their addresses. From there you can peek at a server's logs, wait for one to finish starting, stop a single server, or shut them all down.

## Why this matters

This is what makes the whole system feel modular. Any piece of code, written by anyone, can become a live service in seconds — discoverable, monitored, and restartable — without touching web-server plumbing. Write the recipe; the framework runs the truck.

*Want the details? Flip to Engineer mode.*
