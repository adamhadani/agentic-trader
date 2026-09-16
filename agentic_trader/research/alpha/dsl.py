"""Bounded AST interpreter for causal single-instrument alpha expressions.

Cross-sectional rank/scale require an aligned panel; they are deliberately absent
from this series language. No global statistics or forward filling are allowed.
"""

from __future__ import annotations

import ast
import math
import operator
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise
from typing import ClassVar

import numpy as np
import pandas as pd

from agentic_trader.research.alpha.operators import ALPHA_OPERATORS, MAX_LOOKBACK, OPERATOR_SPECS, safe_div


MAX_EXPRESSION_LENGTH = 4096
MAX_EXPRESSION_NODES = 128
MAX_EXPRESSION_DEPTH = 16
MAX_CONSTANT = 1_000_000
MAX_POWER = 5
FIELD_UNITS = {
    **dict.fromkeys(("open", "high", "low", "close", "vwap"), (1.0, 0.0)),
    "volume": (0.0, 1.0),
    **dict.fromkeys(("returns", "hl_spread", "oc_spread", "open_gap"), (0.0, 0.0)),
}
BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: safe_div,
    ast.Pow: operator.pow,
}
COMPARISONS = {
    ast.Gt: operator.gt,
    ast.Lt: operator.lt,
    ast.GtE: operator.ge,
    ast.LtE: operator.le,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}


class AlphaDSLSyntaxError(ValueError):
    """An expression violates the bounded, causal language contract."""


@dataclass(frozen=True)
class CompiledExpression:
    tree: ast.AST
    required_fields: frozenset[str]
    units: tuple[float, float]
    lookback: int


def _literal(node):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _literal(node.operand)
        return -value if isinstance(node.op, ast.USub) else value
    raise AlphaDSLSyntaxError("Expected a numeric literal")


def _same_units(left, right):
    # Numeric constants may supply an epsilon/threshold in the series' units.
    if left is not None and right is not None and left != right:
        raise AlphaDSLSyntaxError("Incompatible units (price, volume or dimensionless)")
    return left if left is not None else right


@lru_cache(maxsize=2048)
def compile_expression(expression: str) -> CompiledExpression:
    if not expression or len(expression) > MAX_EXPRESSION_LENGTH:
        raise AlphaDSLSyntaxError("Expression length exceeds budget")
    try:
        tree = ast.parse(expression.strip(), mode="eval").body
    except (SyntaxError, RecursionError) as exc:
        raise AlphaDSLSyntaxError("Invalid expression syntax") from exc
    if sum(1 for _ in ast.walk(tree)) > MAX_EXPRESSION_NODES:
        raise AlphaDSLSyntaxError("Expression node budget exceeded")
    fields = set()

    def visit(node, depth=0):
        if depth > MAX_EXPRESSION_DEPTH:
            raise AlphaDSLSyntaxError("Expression depth budget exceeded")
        if isinstance(node, ast.Constant):
            value = _literal(node)
            if not math.isfinite(value) or abs(value) > MAX_CONSTANT:
                raise AlphaDSLSyntaxError("Constant must be finite and bounded")
            return None, 0
        if isinstance(node, ast.Name) and node.id.lower() in FIELD_UNITS:
            name = node.id.lower()
            fields.add(name)
            return FIELD_UNITS[name], 1 if name in ("returns", "open_gap") else 0
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return visit(node.operand, depth + 1)
        if isinstance(node, ast.BinOp) and type(node.op) in BINARY_OPERATORS:
            left, ll = visit(node.left, depth + 1)
            right, rl = visit(node.right, depth + 1)
            if isinstance(node.op, (ast.Add, ast.Sub)):
                units = _same_units(left, right)
            elif isinstance(node.op, ast.Pow):
                power = _literal(node.right)
                if type(power) is not int or abs(power) > MAX_POWER:
                    raise AlphaDSLSyntaxError("Power must be a bounded integer literal")
                units = tuple(u * power for u in (left or (0, 0)))
            else:
                sign = -1 if isinstance(node.op, ast.Div) else 1
                units = tuple(a + sign * b for a, b in zip(left or (0, 0), right or (0, 0), strict=True))
            return units, max(ll, rl)
        if isinstance(node, ast.Compare) and all(type(op) in COMPARISONS for op in node.ops):
            entries = [visit(part, depth + 1) for part in [node.left, *node.comparators]]
            for left, right in pairwise(entries):
                _same_units(left[0], right[0])
            return (0, 0), max(item[1] for item in entries)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            spec = OPERATOR_SPECS.get(node.func.id.lower())
            if spec is None or node.keywords or not spec.minimum_args <= len(node.args) <= spec.maximum_args:
                raise AlphaDSLSyntaxError("Unknown operator, keyword argument or incorrect arity")
            entries = [visit(arg, depth + 1) for arg in node.args]
            lookback = max(item[1] for item in entries)
            if spec.window_argument is not None:
                window = (
                    _literal(node.args[spec.window_argument])
                    if len(node.args) > spec.window_argument
                    else (20 if node.func.id.lower() == "zscore" else 1)
                )
                if type(window) is not int or not 1 <= window <= MAX_LOOKBACK:
                    raise AlphaDSLSyntaxError("Lookback must be a positive bounded integer")
                if entries[0][0] is None:
                    raise AlphaDSLSyntaxError("Rolling operators require series input")
                lookback += window if node.func.id.lower() in ("delay", "delta", "roc") else window - 1
            units = entries[0][0]
            if spec.result_units == "dimensionless":
                units = (0, 0)
            elif spec.result_units == "sqrt":
                units = tuple(u / 2 for u in (units or (0, 0)))
            elif spec.result_units == "branches":
                units = _same_units(entries[1][0], entries[2][0])
            elif spec.result_units == "clip":
                _same_units(entries[0][0], entries[1][0])
                _same_units(entries[0][0], entries[2][0])
                if _literal(node.args[1]) > _literal(node.args[2]):
                    raise AlphaDSLSyntaxError("Invalid clipping bounds")
            elif spec.result_units == "matching":
                units = _same_units(entries[0][0], entries[1][0])
            return units, lookback
        raise AlphaDSLSyntaxError(f"Unsupported expression: {type(node).__name__}")

    units, lookback = visit(tree)
    if lookback > MAX_LOOKBACK:
        raise AlphaDSLSyntaxError("Composed lookback exceeds budget")
    return CompiledExpression(tree, frozenset(fields), units or (0, 0), lookback)


class AlphaExpressionEvaluator:
    SUPPORTED_FIELDS: ClassVar[set[str]] = set(FIELD_UNITS)

    def __init__(self):
        self.operators = ALPHA_OPERATORS

    @classmethod
    def prepare_data_fields(cls, df: pd.DataFrame) -> dict[str, pd.Series]:
        names = [str(c).lower() for c in df.columns]
        if len(names) != len(set(names)):
            raise ValueError("Ambiguous duplicate field names")
        fields = {str(c).lower(): df[c].astype(float) for c in df.columns if str(c).lower() in FIELD_UNITS}
        if "close" in fields:
            close = fields["close"]
            fields["returns"] = close.pct_change(fill_method=None)
            if "open" in fields:
                fields["oc_spread"] = safe_div(close - fields["open"], close)
                fields["open_gap"] = safe_div(fields["open"], close.shift(1)) - 1
            if "high" in fields and "low" in fields:
                fields["hl_spread"] = safe_div(fields["high"] - fields["low"], close)
        return fields

    def validate(self, expression: str) -> bool:
        try:
            compile_expression(expression)
            return True
        except ValueError, TypeError:
            return False

    def evaluate(self, expression: str, data: pd.DataFrame | dict[str, pd.Series]) -> pd.Series:
        compiled = compile_expression(expression)
        fields = self.prepare_data_fields(data) if isinstance(data, pd.DataFrame) else data
        index = data.index if isinstance(data, pd.DataFrame) else next(iter(fields.values())).index
        if not index.is_unique or not index.is_monotonic_increasing:
            raise ValueError("Observations must have unique, sorted timestamps")
        if any(not value.index.equals(index) for value in fields.values()):
            raise ValueError("All input observations must align exactly")
        missing = compiled.required_fields - fields.keys()
        if missing:
            raise ValueError(f"Missing required fields: {sorted(missing)}")

        def evaluate(node):
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.Name):
                return fields[node.id.lower()]
            if isinstance(node, ast.UnaryOp):
                value = evaluate(node.operand)
                return -value if isinstance(node.op, ast.USub) else value
            if isinstance(node, ast.BinOp):
                return BINARY_OPERATORS[type(node.op)](evaluate(node.left), evaluate(node.right))
            if isinstance(node, ast.Compare):
                left = evaluate(node.left)
                result = pd.Series(1.0, index=index)
                for op, part in zip(node.ops, node.comparators, strict=True):
                    right = evaluate(part)
                    step = pd.Series(COMPARISONS[type(op)](left, right), index=index).astype(float)
                    step = step.mask(pd.isna(left) | pd.isna(right))
                    result *= step
                    left = right
                return result
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                args = [evaluate(arg) for arg in node.args]
                result = self.operators[node.func.id.lower()](*args)
                return pd.Series(result, index=index) if isinstance(result, np.ndarray) else result
            raise AlphaDSLSyntaxError("Unexpected compiled node")

        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            result = evaluate(compiled.tree)
        return pd.Series(result, index=index, dtype=float).replace([np.inf, -np.inf], np.nan)
