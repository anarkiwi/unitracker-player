"""Modulator expressions compiled once to Python callables of ``(v, phi, n)`` by
:mod:`unitracker_grammar.expr`, ``//`` and ``%`` 0 by a divisor of 0."""

import ast
import functools
from collections.abc import Callable

from unitracker_grammar import frequency
from unitracker_grammar.expr import compile_expr as _compiled
from unitracker_grammar.expr import indexed, tree

Fn = Callable[[int, int, int], int]


def note_table(clock_hz: int, rounding: str = "round") -> Callable[[int], int]:
    """``F(k)`` at ``clock_hz`` with ``rounding``, cached."""
    return functools.lru_cache(maxsize=None)(
        functools.partial(frequency, clock_hz=clock_hz, rounding=rounding)
    )


def constant(node: ast.expr) -> int | None:
    """The integer an expression is, when it is an integer literal."""
    return node.value if isinstance(node, ast.Constant) else None


def compile_expr(node: ast.expr, table: Callable[[int], int], tables: dict) -> Fn:
    """``node`` as a callable of ``(v, phi, n)``, reading ``F`` from ``table`` and ``T[k]`` from
    ``tables``; the callable's ``node`` is ``node``."""
    namespace = {"_F": table, "_T": {name: indexed(values) for name, values in tables.items()}}
    fn = _compiled(node, namespace, zero=True)
    fn.node = node
    return fn


def offset(n: int) -> ast.expr:
    """``F(N) + n``: the expression of an integer ``freq``."""
    return tree(f"F(N) + {n}")
