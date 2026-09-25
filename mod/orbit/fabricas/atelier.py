"""atelier — the cutting table: catalog, validation, pricing, rendering.

Everything a clothing design *is* lives here, with no I/O and no state:

    GARMENTS   the cuts we sew (silhouette path + print area + base price)
    PALETTE    fabric colors we stock
    INKS       print colors
    SHAPES     graphic marks a design can place
    FONTS      type families a text layer can use

    validate(design)          -> normalized design dict, or ValueError
    quote(design, size, qty)  -> price breakdown, deterministic
    render(design)            -> an SVG mockup string, no network, no deps

A design is a plain dict:

    {"garment": "tee", "color": "indigo", "name": "night shift",
     "layers": [{"type": "text", "text": "MOD", "font": "display",
                 "ink": "gold", "x": 50, "y": 30, "size": 60, "rotate": 0},
                {"type": "shape", "shape": "bolt", "ink": "white",
                 "x": 50, "y": 62, "size": 45, "rotate": 12}]}

Layer x/y/size are percentages of the garment's print area, so the same
design re-renders correctly on any cut. store.py persists these dicts;
mod.py and serve.py just call through.
"""

from xml.sax.saxutils import escape

# ── the cuts ─────────────────────────────────────────────────────────
# Silhouettes live in a 400x400 viewBox. `print` is the printable window
# in those coordinates; layers position themselves inside it in percent.

GARMENTS = {
    'tee': {
        'label': 'classic tee',
        'base_price': 18.0,
        'sizes': ['XS', 'S', 'M', 'L', 'XL', 'XXL'],
        'path': ('M132 60 C150 78 178 88 200 88 C222 88 250 78 268 60 '
                 'L316 78 L368 150 L312 184 L296 158 L296 344 L104 344 '
                 'L104 158 L88 184 L32 150 L84 78 Z'),
        'details': [],
        'print': {'x': 136, 'y': 128, 'w': 128, 'h': 160},
    },
    'longsleeve': {
        'label': 'long sleeve',
        'base_price': 24.0,
        'sizes': ['XS', 'S', 'M', 'L', 'XL', 'XXL'],
        'path': ('M132 60 C150 78 178 88 200 88 C222 88 250 78 268 60 '
                 'L318 80 L352 128 L376 300 L316 310 L300 196 L300 344 '
                 'L100 344 L100 196 L84 310 L24 300 L48 128 L82 80 Z'),
        'details': [],
        'print': {'x': 138, 'y': 128, 'w': 124, 'h': 160},
    },
    'hoodie': {
        'label': 'hoodie',
        'base_price': 42.0,
        'sizes': ['S', 'M', 'L', 'XL', 'XXL'],
        'path': ('M142 70 C142 38 258 38 258 70 L314 92 L366 168 L308 200 '
                 'L294 172 L294 344 L106 344 L106 172 L92 200 L34 168 '
                 'L86 92 Z'),
        'details': [
            # hood opening
            'M158 72 C158 50 242 50 242 72 C242 96 158 96 158 72 Z',
            # kangaroo pocket
            'M144 258 L256 258 L238 326 L162 326 Z',
        ],
        'print': {'x': 142, 'y': 130, 'w': 116, 'h': 110},
    },
    'tote': {
        'label': 'tote bag',
        'base_price': 14.0,
        'sizes': ['ONE'],
        'path': 'M120 140 L280 140 L296 356 L104 356 Z',
        'details': [
            'M146 140 C146 62 254 62 254 140 L232 140 C232 86 168 86 168 140 Z',
        ],
        'print': {'x': 130, 'y': 176, 'w': 140, 'h': 148},
    },
    'cap': {
        'label': 'cap',
        'base_price': 16.0,
        'sizes': ['ONE'],
        'path': 'M80 208 C80 118 320 118 320 208 L320 222 L80 222 Z',
        'details': [
            'M60 214 C160 254 240 254 340 214 L340 236 C240 276 160 276 60 236 Z',
        ],
        'print': {'x': 158, 'y': 150, 'w': 84, 'h': 56},
    },
}

# ── fabric + ink ─────────────────────────────────────────────────────

PALETTE = {
    'bone':     '#e8e2d4', 'white':  '#f4f4f0', 'sand':   '#cbb17a',
    'rose':     '#d8a0a6', 'rust':   '#b0532e', 'olive':  '#57623c',
    'forest':   '#1f3d2b', 'sky':    '#9dbcd4', 'indigo': '#274060',
    'plum':     '#5a3d5c', 'charcoal': '#2b2b2e', 'black': '#101014',
}

INKS = {
    'black': '#101014', 'white': '#f4f4f0', 'gold': '#c9a227',
    'crimson': '#a4243b', 'teal': '#1f7a72', 'cream': '#e8e2d4',
    'navy': '#1d2d44',
}

FONTS = {
    'sans':    'Helvetica, Arial, sans-serif',
    'serif':   'Georgia, "Times New Roman", serif',
    'mono':    '"Courier New", monospace',
    'display': 'Impact, "Arial Black", sans-serif',
}

# Marks in a 100x100 box, centered-ish on (50,50).
SHAPES = {
    'star':    'M50 5 L61 38 L96 38 L68 59 L79 92 L50 72 L21 92 L32 59 L4 38 L39 38 Z',
    'heart':   ('M50 88 C20 64 8 44 14 28 C20 12 42 12 50 28 '
                'C58 12 80 12 86 28 C92 44 80 64 50 88 Z'),
    'bolt':    'M58 4 L20 56 L44 56 L36 96 L80 40 L54 40 Z',
    'moon':    ('M62 6 C34 12 18 34 22 60 C26 84 48 96 68 92 '
                'C48 84 38 66 40 46 C42 28 50 14 62 6 Z'),
    'diamond': 'M50 4 L92 50 L50 96 L8 50 Z',
    'wave':    ('M4 64 C20 40 32 40 46 58 C60 76 74 76 96 46 L96 64 '
                'C76 90 58 88 44 72 C32 58 22 60 6 78 Z'),
    'arrow':   'M8 42 L60 42 L60 20 L96 50 L60 80 L60 58 L8 58 Z',
}

# ── pricing ──────────────────────────────────────────────────────────

PRINT_PRICE = {'text': 2.5, 'shape': 2.0}
SIZE_SURCHARGE = {'XXL': 2.0, 'XXXL': 3.0}
QTY_BREAKS = [(25, 0.20), (10, 0.10)]   # (min qty, discount)
MAX_LAYERS = 8
MAX_TEXT = 40


def _color(value, table, fallback):
    """A palette name, or any #rrggbb the customer brings."""
    if not value:
        return fallback
    if value in table:
        return table[value]
    v = str(value)
    if v.startswith('#') and len(v) in (4, 7) and all(
            c in '0123456789abcdefABCDEF' for c in v[1:]):
        return v
    raise ValueError(f'unknown color {value!r} — stock: {", ".join(table)}')


def validate(design):
    """Normalize a design dict; raise ValueError with the reason if it
    can't be sewn. Returns a clean copy — never mutates the input."""
    if not isinstance(design, dict):
        raise ValueError('a design is a dict — see /catalog for the shape')
    garment = design.get('garment', 'tee')
    if garment not in GARMENTS:
        raise ValueError(f'no cut named {garment!r} — stock: {", ".join(GARMENTS)}')
    layers = design.get('layers') or []
    if len(layers) > MAX_LAYERS:
        raise ValueError(f'{len(layers)} layers — the press takes {MAX_LAYERS}')
    clean_layers = []
    for i, layer in enumerate(layers):
        kind = layer.get('type')
        if kind not in ('text', 'shape'):
            raise ValueError(f'layer {i}: type must be text or shape')
        out = {
            'type': kind,
            'ink': layer.get('ink', 'black'),
            'x': max(0.0, min(100.0, float(layer.get('x', 50)))),
            'y': max(0.0, min(100.0, float(layer.get('y', 50)))),
            'size': max(5.0, min(100.0, float(layer.get('size', 40)))),
            'rotate': float(layer.get('rotate', 0)) % 360,
        }
        _color(out['ink'], INKS, INKS['black'])
        if kind == 'text':
            text = str(layer.get('text', '')).strip()
            if not text:
                raise ValueError(f'layer {i}: text layer with no text')
            if len(text) > MAX_TEXT:
                raise ValueError(f'layer {i}: text caps at {MAX_TEXT} chars')
            out['text'] = text
            font = layer.get('font', 'display')
            if font not in FONTS:
                raise ValueError(f'layer {i}: no font {font!r} — stock: {", ".join(FONTS)}')
            out['font'] = font
        else:
            shape = layer.get('shape', 'star')
            if shape not in SHAPES:
                raise ValueError(f'layer {i}: no mark {shape!r} — stock: {", ".join(SHAPES)}')
            out['shape'] = shape
        clean_layers.append(out)
    color = design.get('color', 'bone')
    _color(color, PALETTE, PALETTE['bone'])
    return {
        'garment': garment,
        'color': color,
        'name': str(design.get('name', '') or 'untitled')[:60],
        'layers': clean_layers,
    }


def quote(design, size='M', qty=1):
    """Deterministic price breakdown for one design in one size × qty."""
    d = validate(design)
    g = GARMENTS[d['garment']]
    qty = max(1, int(qty))
    size = str(size).upper()
    if size not in g['sizes']:
        raise ValueError(f"{d['garment']} comes in {', '.join(g['sizes'])}, not {size}")
    base = g['base_price']
    surcharge = SIZE_SURCHARGE.get(size, 0.0)
    print_cost = sum(PRINT_PRICE[l['type']] for l in d['layers'])
    unit = base + surcharge + print_cost
    discount = next((rate for floor, rate in QTY_BREAKS if qty >= floor), 0.0)
    total = round(unit * qty * (1 - discount), 2)
    return {
        'garment': d['garment'], 'size': size, 'qty': qty,
        'base': base, 'size_surcharge': surcharge,
        'print_cost': round(print_cost, 2), 'layers': len(d['layers']),
        'unit': round(unit, 2), 'discount': discount, 'total': total,
        'currency': 'USD',
    }


def render(design, width=400):
    """The design as an SVG mockup. Pure string building — renders the
    same on the CLI, in the console and in a saved file."""
    d = validate(design)
    g = GARMENTS[d['garment']]
    fabric = _color(d['color'], PALETTE, PALETTE['bone'])
    pa = g['print']
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400" '
        f'width="{int(width)}" height="{int(width)}">',
        f'<path d="{g["path"]}" fill="{fabric}" stroke="#000" '
        f'stroke-opacity="0.35" stroke-width="3" stroke-linejoin="round"/>',
    ]
    for detail in g['details']:
        parts.append(f'<path d="{detail}" fill="#000" opacity="0.14"/>')
    for layer in d['layers']:
        cx = pa['x'] + pa['w'] * layer['x'] / 100
        cy = pa['y'] + pa['h'] * layer['y'] / 100
        ink = _color(layer['ink'], INKS, INKS['black'])
        rot = layer['rotate']
        if layer['type'] == 'text':
            font_px = max(6.0, pa['w'] * layer['size'] / 100 * 0.42)
            parts.append(
                f'<text x="{cx:.1f}" y="{cy:.1f}" fill="{ink}" '
                f'font-family="{FONTS[layer["font"]]}" font-size="{font_px:.1f}" '
                f'text-anchor="middle" dominant-baseline="central" '
                f'transform="rotate({rot:.1f} {cx:.1f} {cy:.1f})">'
                f'{escape(layer["text"])}</text>')
        else:
            k = pa['w'] * layer['size'] / 100 / 100
            parts.append(
                f'<g transform="translate({cx:.1f} {cy:.1f}) rotate({rot:.1f}) '
                f'scale({k:.3f}) translate(-50 -50)">'
                f'<path d="{SHAPES[layer["shape"]]}" fill="{ink}"/></g>')
    parts.append('</svg>')
    return ''.join(parts)


def catalog():
    """Everything the designer console needs in one call: cuts (with
    geometry, so previews compose client-side from the same paths this
    renderer uses), fabric, inks, marks, type."""
    return {
        'garments': GARMENTS, 'palette': PALETTE, 'inks': INKS,
        'shapes': SHAPES, 'fonts': FONTS,
        'pricing': {'print': PRINT_PRICE, 'size_surcharge': SIZE_SURCHARGE,
                    'qty_breaks': QTY_BREAKS},
        'limits': {'max_layers': MAX_LAYERS, 'max_text': MAX_TEXT},
    }
