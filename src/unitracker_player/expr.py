"""Modulator expressions compiled once to Python callables of ``(v, phi, n)``."""

import functools
from collections.abc import Callable

from unitracker_grammar import frequency

Fn = Callable[[int, int, int], int]
_ATOMS = {"v": "v", "phi": "p", "N": "n"}
_BINARY = {"+", "-", "*", "//", "%"}


def note_table(clock_hz: int) -> Callable[[int], int]:
    """``F(k)`` at ``clock_hz``, cached."""
    return functools.lru_cache(maxsize=None)(functools.partial(frequency, clock_hz=clock_hz))


def _at(table: tuple, k: int) -> int:
    return table[k] if 0 <= k < len(table) else 0


def _div(a: int, b: int) -> int:
    return a // b if b else 0


def _mod(a: int, b: int) -> int:
    return a % b if b else 0


def constant(ast: tuple) -> int | None:
    """The integer an expression is, when it is an integer literal."""
    return ast[1] if ast[0] == "int" else None


def source(ast: tuple, tables: dict) -> str:
    """The Python source of ``ast`` over ``v``, ``p`` (φ) and ``n`` (N)."""
    head, args = ast[0], ast[1:]
    if head == "int":
        return repr(args[0])
    if head in _ATOMS:
        return _ATOMS[head]
    if head == "T":
        if args[0] not in tables:
            raise ValueError(f"unknown table {args[0]!r}")
        return f"_at(T_{args[0]}, {source(args[1], tables)})"
    if head == "cmp":
        op, a, b = args
        return f"(1 if {source(a, tables)} {op} {source(b, tables)} else 0)"
    subs = [source(a, tables) for a in args]
    if head == "neg":
        return f"(-{subs[0]})"
    if head in ("//", "%") and constant(args[1]) in (None, 0):
        return f"{'_div' if head == '//' else '_mod'}({subs[0]}, {subs[1]})"
    if head in _BINARY:
        return f"({subs[0]} {head} {subs[1]})"
    return f"{head}({', '.join(subs)})"


def compile_expr(ast: tuple, table: Callable[[int], int], tables: dict) -> Fn:
    """``ast`` as a callable of ``(v, phi, n)``, reading ``F`` from ``table`` and ``T[k]`` from
    ``tables``; Python ints, ``//`` and ``%`` floored and 0 by a divisor of 0."""
    scope = {"F": table, "_at": _at, "_div": _div, "_mod": _mod, "__builtins__": {}}
    scope.update({"abs": abs, "min": min, "max": max})
    scope.update({f"T_{name}": tuple(values) for name, values in tables.items()})
    return eval(f"lambda v, p, n: {source(ast, tables)}", scope)  # pylint: disable=eval-used


def offset(n: int) -> tuple:
    """``F(N) + n``: the expression of an integer ``freq``."""
    return ("+", ("F", ("N",)), ("int", n))
