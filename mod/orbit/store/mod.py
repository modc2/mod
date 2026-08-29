"""
store — share a picture publicly, or as a code that works once and expires.

Two ways to hand somebody an image, and they are deliberately different things:

    publish     a permanent public URL. No credential, no expiry, no audience.
                Anyone who ever sees the link can see the picture, forever.
    grant       a one-time code, good for N seconds. It goes in a QR code on
                your screen, the first person to scan it gets the picture, and
                the second person gets 410. So does the first person twice.

    python3 mod.py help                         # every verb, in one screen
    python3 mod.py share photo.jpg for=5m       # store it AND show the QR
    python3 mod.py add photo.jpg                # store it, private
    python3 mod.py images                       # yours
    python3 mod.py publish sunset.png           # give it a public URL
    python3 mod.py unpublish sunset.png
    python3 mod.py grant latest for=30s         # a code good for 30 seconds
    python3 mod.py qr e54c50db for=30s          # the same, printed as a QR
    python3 mod.py grants                       # codes still live
    python3 mod.py peek <code>                  # would it work? costs nothing
    python3 mod.py revoke <code>                # kill one early
    python3 mod.py claim <code>                 # redeem it (burns it)
    python3 mod.py rm sunset.png
    python3 mod.py docs                         # the manual, as data
    python3 mod.py mcp                          # the tools an agent gets
    python3 mod.py serve                        # api :50670, console :50671

YOU DO NOT HAVE TO TYPE A SHA256
    Anywhere a picture is named you may write its full id, any unique prefix
    of it (four characters is usually plenty), the filename you stored it
    under, or `latest` for the one you added most recently. Two matches is an
    error listing both rather than a guess, because the verbs on the other end
    of this publish things forever and delete things permanently.

    Durations are the same idea: `for=30s`, `for=5m`, `for=2h`, `for=1d`, or a
    bare number of seconds. `ttl_seconds=` still works and still means seconds.

A LINK POINTS AT A PAGE, AND THE PAGE POINTS AT THE PICTURE
    What goes in the QR code is /v/<code>, a page that says what the code is,
    counts its timer down and carries the button that spends it. Loading that
    page claims nothing, so a chat preview or a prefetching browser can no
    longer burn a grant on its way to the person it was for — only somebody
    pressing the button does that. The raw /g/<code> route still exists and
    still burns on fetch, which is what `claim` and `curl` want.
    A published picture has a page too: /p/<id>, next to the bytes at /i/<id>.

WHY THIS IS NOT `m store/...`
    Module names in this protocol are derived from the directory path, and
    `core.tree` applies the orbits in an order that lets `core` overwrite
    `orbit`. There is already a `core/store`, so `m.mod('store')` resolves
    there and always will — this directory is unreachable through the module
    registry no matter what `config.json` says its name is. It was built here
    because that is where it was asked for; it works as an HTTP product on its
    own ports and through this file directly, the way orbit/chain does. If you
    want it callable as `m <name>/fn`, the directory has to be renamed. The
    same collision is why the previous occupant of this path became orbit/shelf.

    Its state lives in `~/.mod/store-share/`, NOT `~/.mod/store/` — that
    directory is core/store's live database, and writing into it would be a
    collision with consequences rather than just a dead end.

THE ONE-TIME PART IS THE POINT
    A grant is burned by a single conditional UPDATE that the database
    adjudicates, not by a read followed by a write, because two phones scanning
    the same code at the same moment is the ordinary case for a QR code on a
    screen and read-then-write hands the picture to both. Time and use are
    independent bounds: never scanning a code is exactly as safe as scanning it
    once, because it dies on the clock either way.

WHAT IT WILL NOT STORE
    Formats are decided by sniffing magic bytes, never by the filename or the
    Content-Type the uploader claims. SVG is refused outright — every other
    image format is inert data, but SVG is a document that can carry script,
    and this is served from an origin shared with the rest of the fleet.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
PORT = 50670       # the API
APP_PORT = 50671   # the console

sys.path.insert(0, str(DIR))
# Aliased on purpose: the class below has methods called `grants` and `qr`, and
# a class-body default like `ttl_seconds=grantlib.DEFAULT_TTL` is evaluated in the
# class namespace — where, after `def grants`, the bare name is the method.
from src import docs as docslib  # noqa: E402
from src import identity, library, links, resolve  # noqa: E402
from src import grants as grantlib  # noqa: E402
from src import mcp as mcplib  # noqa: E402
from src import qr as qrlib  # noqa: E402


class Mod:
    description = __doc__
    path = str(DIR)

    # ── info ─────────────────────────────────────────────────────────

    def forward(self, fn: str = 'info', *args, **kwargs):
        return getattr(self, fn)(*args, **kwargs)

    def info(self):
        """What is on the shelf, and where to reach it."""
        return {
            'name': 'store',
            'what': 'image sharing — public links, and one-time QR grants '
                    'that last N seconds',
            'state': str(library.HOME),
            'api': f'http://127.0.0.1:{PORT}',
            'app': f'http://127.0.0.1:{APP_PORT}/store',
            'share_base': links.BASE,
            'owner': identity.local_owner(),
            'stats': library.stats(),
            'live_grants': len(grantlib.listing(identity.local_owner())),
            'qr': qrlib.available(),
            'mcp_tools': len(mcplib.TOOLS),
            'max_bytes': library.MAX_BYTES,
            'ttl': {'min': grantlib.MIN_TTL, 'max': grantlib.MAX_TTL,
                    'default': grantlib.DEFAULT_TTL},
            'not_a_module': 'orbit/store is shadowed by core/store — '
                            "m.mod('store') is NOT this. See the README.",
        }

    def health(self):
        """Is the index telling the truth about what is on disk."""
        missing = []
        for image in library.public_listing(limit=500):
            if not library.blob_path(image['id']).exists():
                missing.append(image['id'])
        return {'ok': not missing, 'missing_blobs': missing,
                'state': str(library.HOME), 'qr': qrlib.available(),
                **library.stats()}

    def readme(self):
        path = DIR / 'README.md'
        return path.read_text() if path.exists() else None

    # ── pictures ─────────────────────────────────────────────────────

    def add(self, path: str, name: str = '', public: bool = False,
            owner: str = None):
        """Store an image file. Re-adding the same bytes is a no-op."""
        source = Path(path).expanduser()
        if not source.is_file():
            return {'error': f'no such file: {source}'}
        return links.decorate_image(library.put(
            source.read_bytes(), name=name or source.name,
            owner=owner or identity.local_owner(), public=bool(public)))

    def images(self, owner: str = None, limit: int = 100, offset: int = 0):
        """Your pictures, newest first."""
        return [links.decorate_image(r) for r in library.listing(
            owner or identity.local_owner(), limit=limit, offset=offset)]

    def public(self, limit: int = 100, offset: int = 0):
        """Everything anyone on this box has published."""
        return [links.decorate_image(r)
                for r in library.public_listing(limit=limit, offset=offset)]

    def image(self, id: str, owner: str = None):
        """One picture's record. `id` may be a prefix, a name, or `latest`."""
        who = owner or identity.local_owner()
        image_id = resolve.image(id, who, public_too=True)
        record = library.record(image_id, who) or library.public_record(image_id)
        return links.decorate_image(record) if record else {'error': 'no such image'}

    def publish(self, id: str, owner: str = None):
        """Give it a permanent public URL. This is not undoable for anyone
        who already has the link and kept the bytes."""
        who = owner or identity.local_owner()
        return links.decorate_image(
            library.publish(resolve.image(id, who), who, True))

    def unpublish(self, id: str, owner: str = None):
        """Take the public URL away. Copies already made stay made."""
        who = owner or identity.local_owner()
        return links.decorate_image(
            library.publish(resolve.image(id, who), who, False))

    def rm(self, id: str, owner: str = None):
        """Delete your row, its grants, and the bytes if nobody else holds them."""
        who = owner or identity.local_owner()
        return library.remove(resolve.image(id, who), who)

    # ── one-time codes ───────────────────────────────────────────────

    def grant(self, id: str, ttl_seconds=None, owner: str = None, **kwargs):
        """Mint a code good for one fetch, for the next N seconds.

        `for=` is the readable spelling of the same argument: `for=90s`,
        `for=5m`, `for=2h`."""
        who = owner or identity.local_owner()
        seconds = resolve.ttl(kwargs.get('for', ttl_seconds))
        return links.decorate_grant(
            grantlib.create(resolve.image(id, who), who, seconds))

    def qr(self, id: str, ttl_seconds=None, owner: str = None, path: str = '',
           **kwargs):
        """Mint a code and render it as a QR code — written to a file, or ASCII."""
        record = self.grant(id, ttl_seconds, owner, **kwargs)
        if not qrlib.available():
            record['qr'] = 'no encoder on this box (pip install segno) — ' \
                           'the link above still works'
            return record
        # The QR carries the PAGE, not the bytes: a scan should land on
        # something that explains the code, and a link preview that follows it
        # must not be able to spend it.
        if path:
            target = Path(path).expanduser()
            target.write_text(qrlib.svg(record['page_url']))
            record['qr'] = str(target)
            return record
        record['qr'] = qrlib.ascii_art(record['page_url'])
        return record

    def grants(self, owner: str = None, all: bool = False, limit: int = 100):
        """Codes still live — or every code ever, with all=True."""
        return [links.decorate_grant(g) for g in grantlib.listing(
            owner or identity.local_owner(), include_dead=bool(all),
            limit=limit)]

    def _code(self, reference, owner: str = None):
        """A whole code as given, or a prefix of one this box minted.

        Exact first, so a code somebody handed you is never reinterpreted as a
        prefix of one of your own."""
        reference = str(reference or '').strip()
        if reference and grantlib.peek(reference):
            return reference
        return resolve.code(reference, owner or identity.local_owner())

    def peek(self, code: str):
        """Would this code still work? Asking does not spend it."""
        return grantlib.peek(self._code(code)) or {'error': 'no such grant'}

    def claim(self, code: str, out: str = ''):
        """Redeem a code. This BURNS it — the next claim gets nothing."""
        record = grantlib.claim(self._code(code), claimed_by='cli')
        data = library.read(record['image'])
        image = library.record(record['image'], record['owner']) or {}
        if out:
            target = Path(out).expanduser()
            target.write_bytes(data)
            return {**record, 'written': str(target), 'bytes': len(data)}
        return {**record, 'bytes': len(data), 'mime': image.get('mime'),
                'note': 'pass out=<path> to write the picture somewhere'}

    def revoke(self, code: str, owner: str = None):
        """Kill a live code before anyone spends it."""
        who = owner or identity.local_owner()
        return grantlib.revoke(resolve.code(code, who), who)

    # ── the one verb for the common errand ───────────────────────────

    def share(self, what: str, ttl_seconds=None, owner: str = None,
              public: bool = False, **kwargs):
        """
        Hand a picture to one person: store it if needed, mint a code, draw it.

        `what` is a file on this box or anything already in your library — a
        name, an id prefix, `latest`. One call, because "show this to the
        person next to me" is one errand and making it three commands is how
        people end up publishing something instead.

            python3 mod.py share screenshot.png for=2m
            python3 mod.py share latest for=30s

        `public=True` publishes instead, which is the other thing entirely and
        is spelled out in the result so nobody does it by accident.
        """
        who = owner or identity.local_owner()
        source = Path(str(what)).expanduser()
        if source.is_file():
            record = library.put(source.read_bytes(), name=source.name,
                                 owner=who, public=bool(public))
            image_id = record['id']
        else:
            image_id = resolve.image(what, who)
            if public:
                library.publish(image_id, who, True)
            record = library.record(image_id, who)

        if public:
            out = links.decorate_image(record)
            out['shared'] = 'published — permanent, open to anyone with the ' \
                            'link, and unpublishing does not recall copies'
            return out

        grant = self.qr(image_id, ttl_seconds, who, **kwargs)
        grant['shared'] = (
            f"one fetch, {resolve.human_duration(grant['ttl'])} — show the QR "
            f"above, or send the link. Opening it claims nothing; the button "
            f"on it does.")
        grant['picture'] = record['name']
        return grant

    def help(self, verb: str = ''):
        """Every verb and what it costs, or one verb's signature."""
        import inspect
        if verb:
            method = getattr(self, verb, None)
            if not callable(method) or verb.startswith('_'):
                return {'error': f'no verb called {verb!r}',
                        'verbs': self._verbs()}
            return {'verb': verb,
                    'signature': f'{verb}{inspect.signature(method)}',
                    'what': inspect.getdoc(method)}
        return {
            'start_here': 'python3 mod.py share <file> for=5m',
            'naming_a_picture': 'full id, any unique prefix of it, the name '
                                'you stored it under, or `latest`',
            'durations': 'for=30s · for=5m · for=2h · for=1d · or a bare '
                         'number of seconds',
            'verbs': self._verbs(),
            'two_ways_to_share': {
                'grant': 'one person, one fetch, N seconds. The default, and '
                         'what `share` does.',
                'publish': 'everyone, forever, no credential. Deliberate, and '
                           'not undoable for copies already made.'},
            'docs': 'python3 mod.py docs — the full manual as data',
        }

    def _verbs(self):
        import inspect
        out = {}
        for name in dir(self):
            if name.startswith('_'):
                continue
            method = getattr(self, name)
            if not callable(method):
                continue
            summary = (inspect.getdoc(method) or '').strip().split('\n')[0]
            if summary:      # undocumented names are plumbing, not verbs
                out[name] = summary
        return out

    def sweep(self, older_than: float = grantlib.KEEP_DEAD_SECONDS):
        """Forget grants that stopped mattering."""
        return grantlib.sweep(older_than)

    # ── documentation ────────────────────────────────────────────────

    def docs(self, section: str = ''):
        """The manual as data: the two ways to share, every endpoint, the
        CLI, the environment, and what this refuses to store."""
        return docslib.document(section)

    def mcp(self, name: str = ''):
        """What an agent gets — the MCP tool list, or one tool's schema.

        The server itself is `python3 src/mcp.py` on stdio, and the running
        API answers the same JSON-RPC on POST /mcp."""
        if name:
            tool = mcplib.TOOLS.get(name)
            if not tool:
                return {'error': f'no such tool: {name}',
                        'tools': list(mcplib.TOOLS)}
            return {'name': name, 'description': tool['description'],
                    'inputSchema': tool['inputSchema']}
        return mcplib.schema()

    # ── identity ─────────────────────────────────────────────────────

    def whoami(self):
        """Who anonymous callers on this box are filed as."""
        return {'owner': identity.local_owner(), 'state': str(library.HOME)}

    def set_owner(self, address: str):
        """File this box's anonymous work under a protocol address."""
        return {'owner': identity.set_owner(address)}

    # ── serve ────────────────────────────────────────────────────────

    def serve(self, no_app: bool = False, no_api: bool = False,
              host: str = '127.0.0.1', port: int = PORT,
              app_port: int = APP_PORT):
        """Run the API and the console. Loopback unless you say otherwise."""
        processes = []
        env = dict(os.environ)
        env['STORE_SHARE_PORT'] = str(port)
        env['STORE_SHARE_APP_PORT'] = str(app_port)
        if not no_api:
            processes.append(subprocess.Popen(
                [sys.executable, str(DIR / 'src' / 'api' / 'api.py'),
                 '--host', host, '--port', str(port)], env=env))
        if not no_app:
            processes.append(subprocess.Popen(
                [sys.executable, str(DIR / 'src' / 'app' / 'server.py'),
                 '--host', host, '--port', str(app_port),
                 '--api', f'http://127.0.0.1:{port}'], env=env))
        try:
            for process in processes:
                process.wait()
        except KeyboardInterrupt:
            for process in processes:
                process.terminate()
        return {'api': port, 'app': app_port}


def _parse(argv):
    """`add photo.jpg public=True` — positionals, then key=value."""
    args, kwargs = [], {}
    for token in argv:
        if '=' in token and not token.startswith('='):
            key, _, value = token.partition('=')
            if value in ('True', 'true'):
                value = True
            elif value in ('False', 'false'):
                value = False
            elif value.lstrip('-').isdigit():
                value = int(value)
            kwargs[key] = value
        else:
            args.append(token)
    return args, kwargs


if __name__ == '__main__':
    fn = sys.argv[1] if len(sys.argv) > 1 else 'info'
    args, kwargs = _parse(sys.argv[2:])
    try:
        result = Mod().forward(fn, *args, **kwargs)
    except library.StoreError as error:
        print(json.dumps({'error': str(error), 'status': error.status}))
        sys.exit(1)
    if isinstance(result, str):
        print(result)
    else:
        # A QR code is a picture, and a picture escaped into a JSON string is
        # not one. Draw it, then print the record that still carries it, so a
        # human sees the code and a script reading stdout still gets the field.
        if isinstance(result, dict) and '\n' in str(result.get('qr') or ''):
            print(result['qr'])
        print(json.dumps(result, indent=2, default=str))
