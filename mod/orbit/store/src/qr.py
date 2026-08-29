"""
QR rendering, as SVG.

`segno` is a pure-Python QR encoder with no compiled dependency, which is why
it is the one used: this module already refuses to need a daemon, and a share
button that only works where a C toolchain ran is not a share button.

It is imported lazily and its absence is not fatal — the grant is the real
artifact and the code is printable text, so a box without segno can still hand
the link over. Only the picture of the link goes missing.
"""


def available() -> bool:
    try:
        import segno  # noqa: F401
        return True
    except Exception:
        return False


def ascii_art(text: str, border: int = 2) -> str:
    """
    A QR code for `text` as half-block text, returned rather than printed.

    `segno`'s own `terminal()` writes to a stream and returns None, so calling
    it for a value silently yields null — which is what every `qr_ascii` field
    in this module used to carry. Capturing the stream is the whole trick.
    """
    import io

    import segno
    buf = io.StringIO()
    segno.make(text, error='m').terminal(out=buf, border=max(0, int(border)),
                                         compact=True)
    return buf.getvalue()


def svg(text: str, scale: int = 6, border: int = 2, dark: str = '#000000',
        light: str = '#ffffff') -> str:
    """A QR code for `text`, as a standalone SVG document."""
    import io

    import segno
    # error='m' recovers ~15% — the right trade for a code being read off a
    # screen at an angle rather than printed on a box.
    code = segno.make(text, error='m')
    buf = io.BytesIO()
    code.save(buf, kind='svg', scale=max(1, int(scale)),
              border=max(0, int(border)), dark=dark, light=light,
              xmldecl=False, svgns=True)
    return buf.getvalue().decode('utf-8')
