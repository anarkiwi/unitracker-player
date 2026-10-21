"""The native frequency oscillations as one arithmetic function each over a parameter bag.

A shape is ``f(bag, v, t, note)``: the bag (the instrument's ``p0``, merged with the
event's ``!set`` writes), the voice's current word ``v``, the tick ``t`` since its arm, and
the note's base word ``note``; each is one closed form the player re-evaluates each tick.
"""

F16 = 0xFFFF

# pylint: disable=unused-argument


def _steps(bag, t, at_delay=False):
    """The steps applied by tick ``t``: the ticks past the ``delay`` (or from it, when the shape
    steps on the tick the delay elapses), none on a ``skip``."""
    delay = bag.get("delay", 0)
    first = delay if at_delay else delay + 1
    if t < first:
        return 0
    held = len([s for s in (bag.get("skip") or ()) if first <= s <= t])
    return t - first + 1 - held


def set_(bag, v, t, note=0):
    """The note plus the bag's offset, held."""
    return (note + bag.get("offset", 0)) & bag.get("mask", F16)


def ramp(bag, v, t, note=0):
    """The note plus offset, stepping ``step`` per step, clamped to ``max`` when it climbs,
    back at the note once ``ticks`` has run, wrapped to the ``mask``."""
    q = _steps(bag, t)
    ticks = bag.get("ticks")
    if ticks is not None and q >= ticks:
        q = 0
    step = bag.get("step", 0)
    out = q * step
    hi = bag.get("max")
    if hi is not None and step >= 0:
        out = min(out, hi)
    return (note + bag.get("offset", 0) + out) & bag.get("mask", F16)


def slew_to_target(bag, v, t, note=0):
    """The note plus offset, walking ``target`` over the steps of ``step``, the last landing
    on it."""
    q = _steps(bag, t)
    target = bag.get("target", 0)
    step = max(1, abs(bag.get("step", 1)))
    land = (abs(target) + step - 1) // step
    if target == 0 or q >= land:
        out = target
    else:
        out = q * step if target > 0 else -q * step
    return (note + bag.get("offset", 0) + out) & bag.get("mask", F16)


def slew_to_zero(bag, v, t, note=0):
    """The note plus offset, with a ``start`` run back to zero, ``amp`` plus one per step,
    the last landing on zero."""
    amp = bag.get("amp", 0)
    s = bag.get("start", 0)
    if s > 0x7FFF:
        s -= 0x10000
    if amp <= 0 or s == 0:
        out = s
    else:
        sign, s0 = (1, s) if s > 0 else (-1, -s)
        land = s0 // (amp + 1)
        q = _steps(bag, t)
        out = 0 if q > land else sign * (s0 - q * (amp + 1))
    return (note + bag.get("offset", 0) + out) & bag.get("mask", F16)


def _triangle_prefix(k: int, vcmp: int) -> int:
    """The sum of the native vibrato's step signs, step 1 to ``k``: the flip is checked on the
    running time before the step, so the period is 2*extent + 4: extent//2 + 1 plus steps, the
    negative run, and its complement, net zero."""
    if k == 0 or vcmp >= 126:
        return k
    a = vcmp // 2 + 1
    b = 2 * a + (vcmp & 1)
    rem = k % (2 * vcmp + 4)
    if rem <= a:
        return rem
    if rem <= a + b:
        return a - (rem - a)
    return a - b + (rem - a - b)


def triangle(bag, v, t, note=0):
    """The note plus offset, with the native vibrato's step of ``step`` on each step of the
    ``extent``'s period."""
    q = _steps(bag, t, at_delay=True)
    if q == 0:
        return (note + bag.get("offset", 0)) & bag.get("mask", F16)
    amp = abs(bag.get("step", 0)) or 1
    vcmp = bag.get("extent", 128)
    return (note + bag.get("offset", 0) + (amp * _triangle_prefix(q, vcmp)) & F16) & bag.get(
        "mask", F16
    )


SHAPES = {
    "set": set_,
    "ramp": ramp,
    "slew_to_target": slew_to_target,
    "slew_to_zero": slew_to_zero,
    "triangle": triangle,
}
