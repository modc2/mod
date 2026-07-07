# Key Management

*This page explains keys: what they are, why they're your identity in mod, and how they sign, prove, and protect things.*

## What is a key?

A key is your identity, in math form. It comes in two halves.

The private half is a secret only you hold — think of it as your actual signature hand. The public half is derived from it and safe to share; from it comes your address, the short code other people know you by. Anyone can check your work with the public half, but only you can produce it with the private half.

One rule above all: the private half never leaves your machine, and you never share it. Whoever holds it *is* you, as far as the network is concerned.

## One key system, many networks

Different blockchains speak slightly different mathematical dialects. Mod handles the big ones — Ethereum and its relatives (the default), Polkadot-style networks, and Solana — through one consistent interface. You ask for a key, say which flavor you want, and mod does the rest. Same steering wheel, different roads.

## Getting a key

You don't fill out forms. Ask mod for a key by name, and if it doesn't exist yet, one is created on the spot and saved in a hidden folder in your home directory. You can list your keys, rename them, and delete the ones you no longer need.

You can also create a key from a mnemonic — a sequence of ordinary words (usually 12 or 24) that encodes the whole key. Write those words down on paper and you can rebuild your identity on any machine, forever. It's the master copy; guard it like one.

## Signing: proving it was you

Signing is the everyday job of a key. You take any piece of data and stamp it with your private half. The stamp — the signature — is unique to both you and that exact data. Change one character of the data and the stamp no longer matches.

It's like sealing a letter with wax and a ring only you own. Anyone can look at the seal and confirm it's yours; nobody can forge it or move it onto a different letter.

## Verifying: checking someone else's stamp

Verification is the flip side, and anyone can do it. Given some data, a signature, and an address, mod tells you true or false: did the owner of that address really sign exactly this? You can even insist the signature be fresh — reject anything stamped more than a few minutes ago — which stops old messages from being replayed.

This is how modules trust each other without meeting: not by passwords, but by proof.

## Keeping secrets

Keys do one more job: locking data. You can encrypt anything — scramble it so thoroughly that only the right key or password can unscramble it — using the same strong encryption banks rely on.

You can also lock the key files themselves with a password. Then, even if someone copies your computer's disk, your keys stay sealed. You can lock or unlock all of them in one move.

## Why this matters

Everything trustworthy in mod flows from keys. When a module proves who called it, when a payment is authorized, when data is sealed — a key did that. Understand the two halves and the wax seal, and the rest of the system makes sense.

*Want the details? Flip to Engineer mode.*
