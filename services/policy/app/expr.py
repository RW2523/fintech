"""The policy rule expression language (docs/05 §1).

Rules are boolean expressions over a flat context. They are parsed to a Python
AST and then walked by an interpreter that understands only the documented
grammar. Nothing is ever passed to ``eval`` or ``exec``: an unsupported node is
a load-time error, so a pack cannot smuggle in code.

Grammar: literals; dotted identifiers; ``== != < <= > >=``; ``and or not``;
``+ - * /``; and the functions ``min max abs len round clamp coalesce
days_between in all any count``.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

__all__ = ["MISSING", "ExpressionError", "compile_rule", "evaluate", "identifiers"]


class ExpressionError(ValueError):
    """The expression cannot be parsed, or uses something outside the grammar."""


class _Missing:
    """A context key that was not supplied.

    Comparing against it is always False, so a rule over absent data fails
    rather than passing by accident (CLAUDE.md §2.7, fail safe).
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "MISSING"

    def __bool__(self) -> bool:
        return False


MISSING = _Missing()

# `in` is a Python keyword, so `in(x, [...])` cannot be parsed directly. It is
# rewritten to `in_(...)` but only where `in` starts an operand, never where it
# is the membership operator (`state in ['WATCH']`).
_IN_CALL = re.compile(r"(^|[(,]|\b(?:and|or|not)\s+)\s*in\s*\(")

_BIN_OPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
}

_COMPARE: dict[type[ast.cmpop], Callable[[Any, Any], bool]] = {
    ast.Eq: lambda a, b: bool(a == b),
    ast.NotEq: lambda a, b: bool(a != b),
    ast.Lt: lambda a, b: bool(a < b),
    ast.LtE: lambda a, b: bool(a <= b),
    ast.Gt: lambda a, b: bool(a > b),
    ast.GtE: lambda a, b: bool(a >= b),
    ast.In: lambda a, b: bool(a in b),
    ast.NotIn: lambda a, b: bool(a not in b),
}

#: Functions taking already-evaluated arguments.
_SIMPLE: dict[str, Callable[..., Any]] = {
    "min": min,
    "max": max,
    "abs": abs,
    "len": len,
    "round": lambda value, digits=0: round(value, int(digits)),
    "clamp": lambda value, low, high: max(low, min(high, value)),
    "coalesce": lambda *values: next(
        (v for v in values if v is not None and not isinstance(v, _Missing)), None
    ),
    "in_": lambda needle, haystack: bool(haystack) and needle in haystack,
}

#: Functions whose second argument is an expression evaluated per item.
_QUANTIFIERS = {"all", "any", "count"}

#: Rules are written in YAML, where booleans and null are lowercase. Python's
#: parser reads those as bare names, so they are bound here as literals.
_LITERALS: dict[str, Any] = {
    "true": True,
    "false": False,
    "null": None,
    "True": True,
    "False": False,
    "None": None,
}


def _rewrite_in(source: str) -> str:
    def swap(match: re.Match[str]) -> str:
        return f"{match.group(1)}in_("

    return _IN_CALL.sub(swap, source)


def _dotted(node: ast.AST) -> str | None:
    """`member.status` -> "member.status"; anything else -> None."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def compile_rule(source: str) -> ast.Expression:
    """Parse and check a rule. Raises :class:`ExpressionError` if unusable."""
    try:
        tree = ast.parse(_rewrite_in(source), mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"cannot parse rule {source!r}: {exc.msg}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name not in _SIMPLE and name not in _QUANTIFIERS and name != "days_between":
                raise ExpressionError(f"rule {source!r} calls unsupported function {name!r}")
            continue
        if isinstance(node, (ast.Attribute, ast.Name, ast.Load)):
            continue
        allowed = (
            ast.Expression,
            ast.BoolOp,
            ast.And,
            ast.Or,
            ast.UnaryOp,
            ast.Not,
            ast.USub,
            ast.BinOp,
            ast.Compare,
            ast.Constant,
            ast.List,
            ast.Tuple,
            *_BIN_OPS,
            *_COMPARE,
        )
        if not isinstance(node, allowed):
            raise ExpressionError(f"rule {source!r} uses {type(node).__name__}, which is outside the grammar")
    return tree


def identifiers(source: str) -> set[str]:
    """Every dotted context key a rule reads. Used to check `reads` is honest."""
    tree = compile_rule(source)
    found: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and (name := _dotted(node)) is not None:
            found.add(name)
    # drop the prefixes of longer paths: member.status implies member
    return {name for name in found if not any(o != name and o.startswith(name + ".") for o in found)}


class _Interpreter:
    def __init__(self, context: dict[str, Any], scope: dict[str, Any] | None = None) -> None:
        self.context = context
        self.scope = scope or {}

    def run(self, node: ast.AST) -> Any:
        method = getattr(self, f"_{type(node).__name__}", None)
        if method is None:
            raise ExpressionError(f"cannot evaluate {type(node).__name__}")
        return method(node)

    # -- structure ---------------------------------------------------------
    def _Expression(self, node: ast.Expression) -> Any:
        return self.run(node.body)

    def _Constant(self, node: ast.Constant) -> Any:
        return node.value

    def _List(self, node: ast.List) -> list[Any]:
        return [self.run(item) for item in node.elts]

    def _Tuple(self, node: ast.Tuple) -> list[Any]:
        return [self.run(item) for item in node.elts]

    # -- lookups -----------------------------------------------------------
    def _Name(self, node: ast.Name) -> Any:
        if node.id in _LITERALS:
            return _LITERALS[node.id]
        if node.id in self.scope:
            return self.scope[node.id]
        return self.context.get(node.id, MISSING)

    def _Attribute(self, node: ast.Attribute) -> Any:
        key = _dotted(node)
        if key is None:
            raise ExpressionError("only dotted identifiers may be used")
        if key in self.scope:
            return self.scope[key]
        return self.context.get(key, MISSING)

    # -- operators ---------------------------------------------------------
    def _BoolOp(self, node: ast.BoolOp) -> bool:
        values = (self.run(v) for v in node.values)
        if isinstance(node.op, ast.And):
            return all(bool(v) for v in values)
        return any(bool(v) for v in values)

    def _UnaryOp(self, node: ast.UnaryOp) -> Any:
        operand = self.run(node.operand)
        if isinstance(node.op, ast.Not):
            return not bool(operand)
        if isinstance(operand, _Missing):
            return MISSING
        return -operand

    def _BinOp(self, node: ast.BinOp) -> Any:
        left, right = self.run(node.left), self.run(node.right)
        if isinstance(left, _Missing) or isinstance(right, _Missing):
            return MISSING
        operation = _BIN_OPS.get(type(node.op))
        if operation is None:
            raise ExpressionError(f"unsupported operator {type(node.op).__name__}")
        try:
            return operation(left, right)
        except ZeroDivisionError:
            return MISSING

    def _Compare(self, node: ast.Compare) -> bool:
        left = self.run(node.left)
        for operator, comparator in zip(node.ops, node.comparators, strict=True):
            right = self.run(comparator)
            # A comparison touching absent data is False, never True.
            if isinstance(left, _Missing) or isinstance(right, _Missing):
                return False
            compare = _COMPARE.get(type(operator))
            if compare is None:
                raise ExpressionError(f"unsupported comparison {type(operator).__name__}")
            try:
                if not compare(left, right):
                    return False
            except TypeError:
                return False
            left = right
        return True

    # -- calls -------------------------------------------------------------
    def _Call(self, node: ast.Call) -> Any:
        name = node.func.id if isinstance(node.func, ast.Name) else None

        if name in _QUANTIFIERS:
            return self._quantify(name, node)

        if name == "days_between":
            return self._days_between(node)

        function = _SIMPLE.get(name or "")
        if function is None:
            raise ExpressionError(f"unsupported function {name!r}")
        args = [self.run(a) for a in node.args]
        if name != "coalesce" and any(isinstance(a, _Missing) for a in args):
            return MISSING
        try:
            return function(*args)
        except (TypeError, ValueError):
            return MISSING

    def _days_between(self, node: ast.Call) -> Any:
        if len(node.args) != 2:
            raise ExpressionError("days_between takes exactly two dates")
        first, second = (self.run(a) for a in node.args)
        parsed = [_as_date(v) for v in (first, second)]
        if any(p is None for p in parsed):
            return MISSING
        return (parsed[1] - parsed[0]).days  # type: ignore[operator]

    def _quantify(self, name: str, node: ast.Call) -> Any:
        """`all(items, expr)` / `any(items, expr)` / `count(items, expr)`.

        The second argument stays unevaluated and runs once per item, with the
        item's own keys in scope (docs/05 §1).
        """
        if len(node.args) != 2:
            raise ExpressionError(f"{name} takes a list and an expression")
        items = self.run(node.args[0])
        if isinstance(items, _Missing) or not isinstance(items, list):
            return MISSING if name == "count" else False

        predicate = node.args[1]
        results: list[bool] = []
        for item in items:
            scope = dict(item) if isinstance(item, dict) else {"item": item}
            results.append(bool(_Interpreter(self.context, scope).run(predicate)))

        if name == "all":
            return all(results)
        if name == "any":
            return any(results)
        return sum(results)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def evaluate(source: str | ast.Expression, context: dict[str, Any]) -> Any:
    """Evaluate a rule against a flat context."""
    tree = compile_rule(source) if isinstance(source, str) else source
    return _Interpreter(context).run(tree)
