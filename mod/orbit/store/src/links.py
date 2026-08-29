"""
Where a share link points — one answer, for every caller that builds one.

There are four URLs for two pictures-worth of ideas, and the distinction that
matters is PAGE versus BYTES:

    /p/<id>     a page about a published image
    /i/<id>     the published bytes themselves
    /v/<code>   a page about a one-time code. Loading it claims NOTHING.
    /g/<code>   the bytes behind that code. Loading it BURNS the code.

The page is what goes in the QR and what you hand to a person, because a person
who scans a code should land somewhere that says what they are holding — how
long it is good for, that it works exactly once, and a button to save it before
it stops existing. Raw bytes cannot say any of that; they are an image and then
they are gone.

It also fixes the accident this module used to have to shrug at. A QR points at
a URL, and things that are not the recipient follow URLs: chat clients making
previews, browsers prefetching, scanners that open a link twice. When the QR
pointed at /g/ every one of those spent the grant. The page is inert — fetching
it claims nothing — and the claim happens when a human presses the button on
it. A preview bot renders a card that says "one-time picture" and the code is
still good when the person it was for arrives.

The bytes routes are unchanged and still work exactly as they did: `curl` a
/g/ link and you get the picture and burn the code, which is what a script
wants. Nothing here is a redirect — both routes are real.

BASE is the origin those links are built from. It defaults to the console,
because the console proxies the API's share paths under one origin and a phone
that scans a QR code must not learn about a second port.
"""
import os

APP_PORT = int(os.environ.get('STORE_SHARE_APP_PORT', 50671))
BASE = os.environ.get('STORE_SHARE_BASE',
                      f'http://127.0.0.1:{APP_PORT}/store').rstrip('/')


def image_page(image_id: str) -> str:
    """The page for a published image — what to hand to a person."""
    return f'{BASE}/p/{image_id}'


def image_bytes(image_id: str) -> str:
    """The published bytes — what to put in an <img> tag."""
    return f'{BASE}/i/{image_id}'


def grant_page(code: str) -> str:
    """The page for a one-time code. Opening it does not spend the code."""
    return f'{BASE}/v/{code}'


def grant_bytes(code: str) -> str:
    """The bytes behind a one-time code. Fetching this SPENDS it."""
    return f'{BASE}/g/{code}'


def grant_qr(code: str) -> str:
    """A picture of the grant's page link. Rendering it spends nothing."""
    return f'{BASE}/g/{code}/qr'


def decorate_grant(grant: dict) -> dict:
    """Every URL a minted grant has, in the shape the API and CLI both return."""
    code = grant['code']
    return {**grant,
            'url': grant_page(code),
            'page_url': grant_page(code),
            'bytes_url': grant_bytes(code),
            'qr_url': grant_qr(code)}


def decorate_image(record: dict) -> dict:
    """Public URLs on a record, but only when the record is actually public."""
    out = dict(record)
    if record.get('public'):
        out['url'] = image_page(record['id'])
        out['page_url'] = image_page(record['id'])
        out['bytes_url'] = image_bytes(record['id'])
    return out
