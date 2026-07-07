# Smart Contracts — BlocTime Protocol

*This page explains the small set of public, self-running programs that handle mod's money, rewards, and record-keeping.*

## What is a smart contract, anyway?

A smart contract is a program that lives on a blockchain — a public ledger no single person controls. Once it's published, it runs exactly as written, for everyone, with no one able to quietly change the rules. Think of it as a vending machine: you put something in, the machine follows its fixed rules, and out comes the result. No cashier, no favors, no exceptions.

BlocTime is mod's set of these vending machines. They currently run on a test network (a practice version of the real thing, using play money), so everything can be tried safely before it counts.

## Treasury — sharing the revenue

The Treasury is a communal tip jar. Money flows in from around the protocol, and anyone who holds the project's governance token owns a slice of the jar — the more tokens you hold, the bigger your slice. When you want your share, you simply claim it. A set percentage is reserved for the project's owner, and even that can be permanently switched off, handing the whole jar to the community forever.

## Market — credits you can spend

The Market works like a prepaid account at a fair exchange rate. You deposit ordinary digital dollars (stablecoins like USDC — tokens designed to always be worth one US dollar) and receive Market credits in return. A price oracle — a trusted, constantly updated price feed — makes sure the conversion is honest. You can spend credits on services, and providers can charge your account with your permission. Small fees on the way in and out flow back to the Treasury, feeding the communal tip jar.

## BlocTime — rewards for patience

BlocTime is staking: you lock up your tokens for a while and earn extra for your patience. It's like a certificate of deposit at a bank — the longer you agree to leave your money untouched, the better the rate. Lock for a short time and you get roughly what you put in; lock for much longer and your reward can multiply several times over. When the lock period ends, you get your original tokens back.

## TokenGate — the bouncer

TokenGate is the doorman for the whole protocol. It keeps the guest list of which tokens are trusted enough to be accepted, and it knows which price feed to consult for each one. Before the Market or Treasury takes a deposit, they check with the bouncer first. This keeps sketchy or fake tokens out of the system.

## Registry — the public phone book

The Registry is where modules get an official, on-chain listing. You register your module's name along with a link to its description, and the record is stamped into the public ledger — provably yours, with a timestamp no one can fake. You can update the listing, hand it to someone else, or remove it. Names only need to be unique per person, so two creators can each have a module called "hello."

## Perms — who's allowed to do what

Perms is a simple family-tree of permissions. Any key can have child keys attached to it, and whoever attaches the first child becomes that branch's owner. Owners can add or remove children and pass the branch to someone else. It's a flexible way to say, on the public record, "these keys belong under this one."

## Safe — the shared vault

For money that belongs to a team rather than a person, there's the Safe: a vault that needs several keyholders to turn their keys at once. No single member can move the funds alone — a transaction only goes through when enough people have signed off. It's the standard way groups protect a shared treasury.

## Why this matters

Together, these contracts mean the important stuff — who owns what, who gets paid, who's allowed in — isn't stored on someone's private server where it could be edited or lost. It's out in the open, enforced by code, and the same for everyone.

*Want the details? Flip to Engineer mode.*
