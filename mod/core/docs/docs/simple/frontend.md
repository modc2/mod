# Frontend App

*This page explains the web app — the face of mod — and how a page in your browser safely talks to a blockchain.*

## What is it?

The frontend is the website you actually see and click. Everything else in mod runs quietly in the background; this is the storefront. From one place you can browse and launch modules, chat with an AI, manage shared funds, watch transactions, and read the docs.

It's built with modern, widely used web tools, and it talks to the Base network — currently its test version, a rehearsal stage where everything works like the real thing but the money is play money. You get to try everything with zero risk.

## How the app is organized

Think of the app as a building with many rooms off one hallway. Each area — home, chat, apps, treasury, contracts, and so on — is its own room, its own page. A shared frame holds it all together:

- A top bar for connecting your wallet and switching between light and dark mode
- A sidebar on the left for moving between rooms
- The main area, where the current room fills the space
- An optional split screen, so you can keep two rooms open side by side

## How the app remembers things

As you move room to room, some facts should follow you: who you are, whether your wallet is connected, your balances, your theme choice. The app keeps these in shared layers that wrap around every page — like the building's electricity and plumbing. Any room can tap into them; no room has to rebuild them.

## Talking to the blockchain

Here's the part that makes this different from an ordinary website. When you check a balance or move funds, the app doesn't send your request to some company's server. It talks to contracts — small programs living on the blockchain that no one can quietly change — at fixed, published addresses.

Your wallet (a browser extension like MetaMask) is the go-between. Think of it as a personal notary standing beside you. The app drafts the transaction, but nothing happens until the notary shows it to you and you approve. The app never sees your secret key; it can only ask, never act alone.

Through this, the app lets you check balances, deposit and withdraw, and send tokens to others — each action approved by you, one signature at a time.

## Where the settings live

The addresses of those on-chain contracts, and which network to use, sit in one plain settings file. Point the file at different addresses and the same app works against a different deployment. Nothing is buried in the code.

## Running it yourself

The frontend is an ordinary web project. Install its dependencies, start it in development mode, and it appears in your browser on your own machine. Edit a file and the page updates as you save.

## Why this matters

A blockchain without a good interface is a vault without a door. This app is the door: it turns raw contracts and cryptography into pages, buttons, and clear confirmations — while keeping the one thing that matters, your key, entirely in your hands.

*Want the details? Flip to Engineer mode.*
