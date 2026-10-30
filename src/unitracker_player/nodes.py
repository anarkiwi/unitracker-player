"""The operator and operands of an expression node of :mod:`unitracker_grammar.expr`."""

import ast

from unitracker_grammar.expr import _BINARY, _BITWISE, _COMPARE, _SHIFT

_BINARIES = _BINARY | _BITWISE | _SHIFT
_UNARIES = {ast.USub: "neg", ast.Invert: "~", ast.Not: "!"}
_LOGIC = {ast.And: "&&", ast.Or: "||"}


def op(node) -> str:
    """The node's operator: a grammar operator, ``neg``, a call's name, ``T`` or ``leaf``."""
    if isinstance(node, ast.BinOp):
        return _BINARIES[type(node.op)]
    if isinstance(node, ast.Compare):
        return _COMPARE[type(node.ops[0])]
    if isinstance(node, ast.UnaryOp):
        return _UNARIES[type(node.op)]
    if isinstance(node, ast.BoolOp):
        return _LOGIC[type(node.op)]
    if isinstance(node, ast.Call):
        return node.func.id
    if isinstance(node, ast.Subscript):
        return "T"
    return "leaf"


def children(node) -> list:
    """The node's operands in order."""
    if isinstance(node, ast.BinOp):
        return [node.left, node.right]
    if isinstance(node, ast.Compare):
        return [node.left, node.comparators[0]]
    if isinstance(node, ast.UnaryOp):
        return [node.operand]
    if isinstance(node, ast.BoolOp):
        return list(node.values)
    if isinstance(node, ast.Call):
        return list(node.args)
    if isinstance(node, ast.Subscript):
        return [node.slice]
    return []
