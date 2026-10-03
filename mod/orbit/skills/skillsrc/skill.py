"""skill — what a skill IS, in one file.

A skill is a document, not a program. It is markdown with YAML front matter:

    ---
    name: pdf
    description: Fill, split and read PDF forms
    license: Apache-2.0
    tools: [bash, read, write]
    ---
    ## When to use this
    ...

That is the whole format, and it is deliberately the same one Claude Code,
the anthropics/skills catalog and this fleet's own `skill.md` files already
use — so a skill scraped off GitHub is installable here without a converter,
and a skill written here is readable there.

Nothing in a skill is executed. The body is instructions handed to a model as
context; `tools:` names tools the agent already has, it does not ship them.
That is the security model in one sentence: a marketplace of documents cannot
run code on the box that browses it.
"""
import hashlib
import re
from typing import Any, Dict, List, Optional

FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)
HEADING = re.compile(r"^#\s+(.+)$", re.M)
MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")

# a skill body over this is truncated on install: the point of a skill is that
# it fits in a prompt beside the actual job
MAX_BODY = 60_000


def slug(text: str) -> str:
    """A catalog name: lowercase, hyphenated, no path tricks."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return (s or "skill")[:64]


def parse_frontmatter(md: str) -> Dict[str, Any]:
    """Scalar + list YAML front matter, and nothing else.

    Skill front matter is flat by convention, so this is a 20-line parser
    rather than a yaml dependency pointed at text pulled off the internet.
    Inline lists (`tools: [a, b]`) and block lists both work.
    """
    m = FRONTMATTER.match(md or "")
    if not m:
        return {}
    out: Dict[str, Any] = {}
    key = None
    block: List[str] = []          # lines of a `key: |` block scalar, if open
    block_key = None

    def close_block():
        nonlocal block, block_key
        if block_key:
            out[block_key] = " ".join(l.strip() for l in block if l.strip())
        block, block_key = [], None

    for line in m.group(1).splitlines():
        if block_key is not None:
            # a block scalar runs until a line that is not indented
            if not line.strip() or line[:1] in (" ", "\t"):
                block.append(line)
                continue
            close_block()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^\s+-\s+", line) and key:
            out.setdefault(key, [])
            if isinstance(out[key], list):
                out[key].append(line.split("-", 1)[1].strip().strip("'\""))
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        key, v = k.strip(), v.strip().strip("'\"")
        # `description: |-` / `>` — the value is the indented lines that follow,
        # which is how half the catalog writes a long description
        if v in ("|", "|-", "|+", ">", ">-", ">+"):
            block_key, block = key, []
            continue
        if v.startswith("[") and v.endswith("]"):
            out[key] = [p.strip().strip("'\"") for p in v[1:-1].split(",") if p.strip()]
        else:
            out[key] = v if v else []
    close_block()
    return out


def strip_frontmatter(md: str) -> str:
    return FRONTMATTER.sub("", md or "", count=1).lstrip()


def summarize(body: str, limit: int = 300) -> str:
    """First real sentence of a body, for a card that has no description."""
    text = strip_frontmatter(body)
    text = MD_IMAGE.sub("", text)
    text = MD_LINK.sub(r"\1", text)
    for para in text.split("\n\n"):
        line = " ".join(l.strip() for l in para.splitlines()
                        if l.strip() and not l.lstrip().startswith(("#", ">", "|", "```")))
        if len(line) > 20:
            return line[:limit].strip()
    return ""


def title_of(body: str) -> str:
    m = HEADING.search(strip_frontmatter(body))
    return m.group(1).strip() if m else ""


def fingerprint(body: str) -> str:
    """Content id — two installs of the same document are the same skill."""
    return hashlib.sha256((body or "").encode("utf-8", "replace")).hexdigest()[:16]


def normalize(body: str, *, name: str = "", description: str = "",
              source: str = "", url: str = "", origin_id: str = "",
              license: str = "", tags: List[str] = None,
              tools: List[str] = None) -> Dict[str, Any]:
    """A fetched document → the catalog record.

    Front matter wins over the caller's guess (the document knows its own
    name), the caller's guess wins over what can be read off the body, and
    the body's own heading is the last resort. A skill with no description
    is close to useless to a model choosing between twenty of them, so one
    is always synthesized.
    """
    full = body or ""
    body = full[:MAX_BODY]
    fm = parse_frontmatter(body)
    fm_tools = fm.get("tools") or fm.get("allowed-tools") or fm.get("allowed_tools") or []
    if isinstance(fm_tools, str):
        fm_tools = [t.strip() for t in re.split(r"[,\s]+", fm_tools) if t.strip()]
    fm_tags = fm.get("tags") or fm.get("keywords") or []
    if isinstance(fm_tags, str):
        fm_tags = [t.strip() for t in fm_tags.split(",") if t.strip()]
    n = str(fm.get("name") or name or title_of(body) or "skill")
    desc = str(fm.get("description") or description or summarize(body) or "")
    return {
        "name": slug(n),
        "title": n.strip(),
        "description": desc.strip()[:600],
        "body": body,
        "tools": [str(t) for t in (tools or fm_tools)][:24],
        "tags": sorted({str(t).lower() for t in list(fm_tags) + list(tags or []) if t})[:12],
        "license": str(fm.get("license") or license or "") or None,
        "version": str(fm.get("version") or "") or None,
        "source": source,
        "url": url,
        "origin_id": origin_id,
        "chars": len(body),
        "truncated": len(full) > MAX_BODY,
        "fingerprint": fingerprint(body),
    }


def to_markdown(rec: Dict[str, Any]) -> str:
    """The catalog record back out as a SKILL.md — what an agent is handed.

    Round-trips: normalize(to_markdown(rec)) is rec, minus provenance. A skill
    installed here can be copied into any other tool that reads SKILL.md.
    """
    fm = [f"name: {rec.get('name', '')}",
          f"description: {rec.get('description', '')}"]
    if rec.get("license"):
        fm.append(f"license: {rec['license']}")
    if rec.get("version"):
        fm.append(f"version: {rec['version']}")
    if rec.get("tools"):
        fm.append("tools: [%s]" % ", ".join(rec["tools"]))
    if rec.get("tags"):
        fm.append("tags: [%s]" % ", ".join(rec["tags"]))
    if rec.get("url"):
        fm.append(f"source: {rec['url']}")
    return "---\n%s\n---\n\n%s" % ("\n".join(fm), strip_frontmatter(rec.get("body", "")))


def looks_like_skill(body: str) -> bool:
    """Is this document actually a skill, or a README that happened to be there?

    Front matter with a name+description is the strong signal. Failing that,
    a document that tells someone how to do something (a heading and enough
    prose) still passes — plenty of good skills in the wild are just that.
    """
    fm = parse_frontmatter(body)
    if fm.get("name") and fm.get("description"):
        return True
    text = strip_frontmatter(body)
    return bool(HEADING.search(text)) and len(text) > 200
