"""bt6 — a launcher/interface for the BT6 arsenal.

BT6 (bt6.gg) is an independent frontier-AI red team stewarded by Pliny the
Liberator. Their public "arsenal" is a set of open red-teaming, transparency
and prompt-engineering tools. This module is a thin catalog + web interface
that gathers every one of those apps behind a single console — nothing here
runs an exploit; it is a directory/launcher that links out to each tool.

The canonical arsenal list lives in `ARSENAL` below so the API, the MCP
surface and the frontend all render from one source of truth.
"""
import os
import mod as m

# One source of truth for the arsenal. Each entry is a public Pliny/BT6 app.
ARSENAL = [
    {
        "id": "l1b3rt4s",
        "name": "L1B3RT4S",
        "url": "https://github.com/elder-plinius/L1B3RT4S",
        "tag": "jailbreaks",
        "blurb": "The liberation library — freedom prompts and jailbreak techniques for every major frontier model.",
    },
    {
        "id": "cl4r1t4s",
        "name": "CL4R1T4S",
        "url": "https://github.com/elder-plinius/CL4R1T4S",
        "tag": "transparency",
        "blurb": "System-prompt transparency: the leaked, verified system prompts behind ChatGPT, Claude, Gemini, Grok and friends.",
    },
    {
        "id": "godm0d3",
        "name": "G0DM0D3",
        "url": "https://godmode.space",
        "tag": "demo",
        "blurb": "A public demonstration of what frontier models will say once the alignment scaffolding gives way — reproducible, shippable.",
    },
    {
        "id": "0bl1t3r4tus",
        "name": "0BL1T3R4TUS",
        "url": "https://github.com/elder-plinius/OBLITERATUS",
        "tag": "abliteration",
        "blurb": "Strip refusal behavior out of open-weight LLMs without retraining — refusal-direction ablation, packaged.",
    },
    {
        "id": "t3mp3st",
        "name": "T3MP3ST",
        "url": "https://github.com/elder-plinius/T3MP3ST",
        "tag": "agents",
        "blurb": "Multi-agent platform for autonomous red-teaming and pentesting — a storm of operators against a target.",
    },
    {
        "id": "st3gg",
        "name": "ST3GG",
        "url": "https://ste.gg",
        "tag": "covert-channel",
        "blurb": "Steganography toolkit — multimodal payload carriers (text, image, audio, multi-layer encoding) safety filters were never trained to see.",
    },
    {
        "id": "p4rs3lt0ngv3",
        "name": "P4RS3LT0NGV3",
        "url": "https://elder-plinius.github.io/P4RS3LT0NGV3/",
        "tag": "payloads",
        "blurb": "Advanced prompt-engineering and payload-crafting reference — speak the serpent's tongue.",
    },
    {
        "id": "l34khvb",
        "name": "L34KHVB",
        "url": "https://leakhub.ai",
        "tag": "community",
        "blurb": "Community hub for submitting and browsing leaked AI system prompts.",
    },
    {
        "id": "v3sp3r",
        "name": "V3SP3R",
        "url": "https://github.com/elder-plinius/V3SP3R",
        "tag": "hardware",
        "blurb": "AI-powered hardware-hacking companion with smart-glasses integration.",
    },
    {
        "id": "gl0ss0p3tr43",
        "name": "GL0SS0P3TR43",
        "url": "https://elder-plinius.github.io/GLOSSOPETRAE/",
        "tag": "encoding",
        "blurb": "Procedural constructed-language generator with steganographic encoding baked in.",
    },
    {
        "id": "gl4ss",
        "name": "GL4SS",
        "url": "https://github.com/elder-plinius/GL4SS",
        "tag": "vision",
        "blurb": "Spatiotemporal image and video engine — render any place, any period.",
    },
    {
        "id": "3nth34",
        "name": "3NTH34",
        "url": "https://elder-plinius.github.io/ENTHEA/",
        "tag": "visuals",
        "blurb": "Real-time psychedelic visuals and music-reactive rendering engine.",
    },
    {
        "id": "plinytv",
        "name": "PL1NY.TV",
        "url": "https://pliny.tv",
        "tag": "media",
        "blurb": "AI-curated dispatches from latent space.",
    },
    {
        "id": "bt6",
        "name": "BT6",
        "url": "https://bt6.gg",
        "tag": "collective",
        "blurb": "The independent frontier-AI red team itself — where a missed exploit is measured in trust, market cap, or national security.",
    },
    {
        "id": "basi",
        "name": "BASI Community",
        "url": "https://discord.gg/basi",
        "tag": "community",
        "blurb": "The BASI Discord — home base for AI red-teamers and prompt engineers.",
    },
]


class Mod:
    description = "BT6 arsenal launcher — one interface for every app in the Pliny / BT6 red-team toolkit."
    path = os.path.dirname(os.path.abspath(__file__))

    def forward(self, **kwargs):
        """Default entry point. A null call returns module info."""
        return self.info()

    def info(self):
        """Return module info."""
        return {
            "name": "bt6",
            "description": self.description,
            "source": "https://bt6.gg/#arsenal",
            "steward": "Pliny the Liberator",
            "count": len(ARSENAL),
            "path": self.path,
        }

    def arsenal(self, tag: str = None):
        """Return the arsenal catalog, optionally filtered by tag."""
        items = ARSENAL
        if tag:
            items = [a for a in ARSENAL if a["tag"] == tag]
        return {"count": len(items), "items": items}

    def tags(self):
        """Return the distinct tags across the arsenal."""
        return sorted({a["tag"] for a in ARSENAL})

    def get(self, id: str):
        """Return a single arsenal entry by id, or None."""
        for a in ARSENAL:
            if a["id"] == id:
                return a
        return None

    def readme(self):
        """Return the project README."""
        for name in ["README.md", "readme.md", "README"]:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        return None
