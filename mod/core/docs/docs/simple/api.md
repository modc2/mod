# API Server

*This page explains the API server — the part of mod that publishes your work, keeps track of versions, and connects to money and ownership on the blockchain.*

## What is the API server?

Think of it as the post office of mod. When you build something and want the world to be able to find it, the API server is where you take it. It stamps your work, files it away safely, and hands out copies to anyone who asks.

It does three big jobs: it publishes modules, it remembers every version of them, and it talks to the blockchain when ownership or payment is involved.

## Publishing your work

When you publish (the docs call it "registering") a module, a few things happen behind the scenes:

1. Your files are uploaded to IPFS — a shared public hard drive that no one owns. Anyone in the world can fetch from it, and nothing on it can be quietly changed.
2. IPFS hands back a fingerprint of your files, called a CID. If even one letter of your work changes, the fingerprint changes too. That makes it tamper-proof.
3. A short entry is written into a public directory: your module's name, its fingerprint, the date, and a note about what's new.
4. You sign that entry with your key — like signing your name on the flap of the envelope, so everyone knows it really came from you.

You can publish something sitting on your computer, something living on GitHub, or something already on IPFS. The server figures out which one you handed it.

## Versions you can always go back to

Every time you publish, a new snapshot is saved — the old ones never disappear. It works like a photo album: each publish adds a new photo, and you can flip back to any earlier one at any moment.

- Made a mistake? Roll back to a previous version.
- Like someone else's module? Fork it — take your own copy and make it yours, with credit built in.
- Done with something? You can remove it from the directory.

## The public directory

The registry is a simple public phone book: it lists every person (by their wallet address) and the modules they've published. Anyone can look up who made what, browse the full list, or pull down the exact files behind any entry.

## Where the blockchain comes in

Most of mod works without touching a blockchain at all. But for the parts that involve ownership and money, the API server connects to smart contracts on Base — a fast, low-cost network built on Ethereum.

Through it you can check token balances, buy credits to spend inside the system, and record your module's name on-chain — a public, permanent claim that this name belongs to you, like registering a trademark.

## A little AI help, too

The server can also call in an AI assistant to edit a module for you. You describe the change in plain English — "add error handling here" — and it makes the edit, step by step.

## Why this matters

Publishing software usually means trusting a company's app store. Here, your work lives on a shared network, signed by you, with every version preserved and your ownership recorded in public. No middleman can take it down or claim it was theirs.

*Want the details? Flip to Engineer mode.*
