# Storage

*This page explains the three ways mod saves your data: a quick pocket, an organized filing cabinet, and a public library that never forgets.*

## The quick pocket

For everyday saving, mod gives you two verbs: put and get. Put stores anything under a name; get brings it back later. That's it.

Everything lands in a hidden folder in your home directory, saved as plain, readable files. Each saved item remembers when it was written, so you can say "only give me this if it's less than an hour old" — perfect for cached data that goes stale. You can also lock any item with a password, and it's scrambled with strong encryption before it touches the disk.

It's the pocket of your jeans. Fast to reach into, fine for most things, no ceremony.

## The filing cabinet

When you outgrow the pocket, there's the Store — same idea, more organization. It's a filing cabinet with drawers and folders.

With it you can:

- File things under nested paths, like folders inside folders
- List everything you've saved, or search for items by name
- See stats at a glance: how big each item is, how old, whether it's locked
- Lock or unlock individual items, whole folders, or everything at once
- Run a private cabinet where every new item is automatically locked the moment it's filed

Folder locking is especially handy: an entire folder collapses into one sealed envelope, and unlocking restores it exactly as it was.

## The public library

The pocket and the cabinet live on your machine. Sometimes you want data to live everywhere — shared, permanent, and not dependent on any single computer. That's IPFS (a shared public hard drive that no one owns).

Here's the twist: IPFS doesn't file things by name or location. It files them by fingerprint. When you store something, you get back a CID — a unique fingerprint computed from the content itself. Anyone, anywhere, who has that fingerprint can fetch exactly that data. And because the fingerprint is made from the content, nobody can quietly swap the contents; a change would change the fingerprint.

Think of it like a library where the call number is a fingerprint of the book. Same book, same number, forever — no matter which branch you visit.

You can "pin" things you care about, which tells your local library branch: keep a copy of this on the shelf, don't let it get cleared out. Mod handles the machinery for you — it installs and runs the IPFS software, checks its health every half minute, and restarts it if it stumbles.

## They work together

The nicest part: the quick pocket understands fingerprints. If you ask get for something that looks like a CID, mod fetches it from the public library automatically. Local and global storage feel like one system.

## Why this matters

Most data wants to be nearby and fast — that's the pocket and the cabinet. Some data wants to be permanent and shared — that's the library. Mod gives you all three with the same two verbs: put and get.

*Want the details? Flip to Engineer mode.*
