"""Pure orientation math for @ref. No I/O, no deps — just angles.

A reference image's pose is three angles, the way a gimbal holds a model:

    yaw    — heading around the vertical axis. 0 = facing the viewer,
             +90 = facing viewer-right (subject's left), ±180 = facing away.
    pitch  — elevation. +90 = seen from directly above, -90 = from below.
    roll   — head/camera tilt. + = tilted toward viewer-right.

All angles are degrees. Matching is done on the *facing direction* (the
point on the sphere yaw/pitch names) plus a discounted roll term, because
an artist hunting a 3/4 view cares where the subject faces far more than
how the head is tilted.
"""

import math

IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff')


def wrap180(a):
    """Fold any angle into (-180, 180]."""
    a = float(a) % 360.0
    if a > 180.0:
        a -= 360.0
    return a


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def normalize(yaw, pitch, roll):
    """One canonical spelling of a pose: yaw/roll wrapped, pitch clamped."""
    return wrap180(yaw), clamp(float(pitch), -90.0, 90.0), wrap180(roll)


def facing(yaw, pitch):
    """Unit vector of the direction the subject faces (viewer space)."""
    y, p = math.radians(yaw), math.radians(pitch)
    return (math.sin(y) * math.cos(p), math.sin(p), math.cos(y) * math.cos(p))


def distance(a, b, roll_weight=0.5):
    """How far apart two poses are, in degrees.

    Great-circle angle between the two facing directions, plus
    ``roll_weight`` times the wrapped roll difference. 0 = identical view.
    """
    ay, ap, ar = normalize(*a)
    by, bp, br = normalize(*b)
    va, vb = facing(ay, ap), facing(by, bp)
    dot = clamp(sum(x * y for x, y in zip(va, vb)), -1.0, 1.0)
    face_deg = math.degrees(math.acos(dot))
    roll_deg = abs(wrap180(ar - br))
    return face_deg + float(roll_weight) * roll_deg


def describe(yaw, pitch, roll):
    """A pose in an artist's words: '3/4 left, from above, tilted right'."""
    yaw, pitch, roll = normalize(yaw, pitch, roll)
    ay = abs(yaw)
    side = 'right' if yaw > 0 else 'left'
    if ay <= 15:
        head = 'front'
    elif ay <= 60:
        head = f'3/4 {side}'
    elif ay <= 120:
        head = f'profile {side}'
    elif ay <= 165:
        head = f'rear 3/4 {side}'
    else:
        head = 'back'
    parts = [head]
    if pitch >= 60:
        parts.append('top-down')
    elif pitch >= 15:
        parts.append('from above')
    elif pitch <= -60:
        parts.append('worm’s-eye')
    elif pitch <= -15:
        parts.append('from below')
    if abs(roll) > 10:
        parts.append(f'tilted {"right" if roll > 0 else "left"}')
    return ', '.join(parts)
