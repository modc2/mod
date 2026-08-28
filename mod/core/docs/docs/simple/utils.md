# Utilities Reference

*This page introduces mod's toolbox of small, everyday helpers — the quiet functions the whole framework leans on.*

## What are utilities?

Every well-run workshop has a drawer of reliable hand tools: a good screwdriver, a tape measure, a level. Utilities are mod's version of that drawer — a few hundred small, battle-tested helpers that do one job each and do it well. Everything else in mod is built on top of them, and you can reach into the drawer yourself whenever you need one.

Here's what's inside, shelf by shelf.

## Reading and writing files

Saving and loading data is the most common chore in any program, so mod makes it one step. There are helpers for plain text, for JSON and YAML (two popular formats for structured settings and data), and even one that reads every file in a folder at once. Copying, moving, and deleting files are each a single call too. No ceremony — just "read this" and "save that."

## Talking to the network

When you run a service on your computer, it listens on a port — think of ports as numbered doors into your machine. These helpers check whether a door is already in use, find you a free one, or clear out whatever's occupying one you need. There's also a quick way to look up your computer's public address on the internet.

## Translating data between shapes

Programs constantly need to convert data from one form to another — a Python object into text you can save, text back into an object, raw bytes into something readable. These helpers handle the translation both ways, and they're smart about it: hand them the text "42" and you get the number back, not just more text. There's also a fingerprinting tool that turns any data into a short unique code (a hash), handy for checking that two things are truly identical.

## Checking the machine's vitals

A quick health check for the computer itself: how many processor cores it has, how much memory is in use, how full the disk is, whether there's a graphics card and how busy it is. Useful before starting heavy work — like glancing at the fuel gauge before a road trip.

## Doing several things at once

Some jobs are faster when you don't wait in line. These helpers let a program run many tasks side by side — like a kitchen where several burners cook at once instead of one pot at a time. You can start background tasks, wait for a batch to finish, or handle each result the moment it's ready.

## Running commands

Sometimes the easiest way to get something done is to type the command you'd type in a terminal — install this, list that. The command helper runs it for you from inside a program, and lets you watch the output live, set a time limit, or run it from a particular folder.

## Lists, luck, and averages

Small tools for working with collections: split a long list into evenly sized batches, shuffle it, pick a random item, or draw a random sample. Plus the basic statistics you reach for constantly — average, middle value, and how spread out the numbers are.

## Telling the user what's happening

Good tools narrate their work. These helpers print colored messages — green for success, red for errors, yellow for warnings — and show a little animated spinner while something slow is running, so nobody wonders whether the program froze.

## When things go wrong

Two favorites here. The first turns a crash into a clear report: which file, which line, what happened. The second is the retry helper — wrap it around anything flaky, like a shaky internet call, and it automatically tries again a few times before giving up. Like knocking three times before deciding no one's home.

## Keeping time and staying stocked

A timer wraps any block of work and tells you exactly how long it took — great for finding the slow part. And the package helpers make sure any library your code depends on is actually installed, quietly fetching it if it's missing, so "works on my machine" becomes "works on every machine."

## Why this matters

None of these tools is impressive on its own. That's the point. They remove the hundred tiny frictions of programming so the code you write can stay focused on the interesting part — and so every module in mod solves these small problems the same reliable way, once.

*Want the details? Flip to Engineer mode.*
