from __future__ import annotations

import ast
import logging
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from agentic_trader.research.alpha.operators import ALPHA_OPERATORS, safe_div


logger = logging.getLogger(__name__)


class AlphaDSLSyntaxError(ValueError):
    """Raised when an alpha expression contains invalid syntax or unauthorized AST nodes."""


class AlphaExpressionEvaluator:
    """
    Safe AST-based parser and vectorized evaluator for Formulaic Alpha DSL expressions.
    Guarantees strict sandboxing: no arbitrary code execution (no eval/exec).
    """

    SUPPORTED_FIELDS: ClassVar[set[str]] = {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "vwap",
        "returns",
        "hl_spread",
        "oc_spread",
    }

    def __init__(self) -> None:
        self.operators = ALPHA_OPERATORS

    @classmethod
    def prepare_data_fields(cls, df: pd.DataFrame) -> dict[str, pd.Series]:
        """Normalize dataframe columns and construct derived feature fields."""
        fields: dict[str, pd.Series] = {}
        col_map = {c.lower(): c for c in df.columns}

        for std_col in ["open", "high", "low", "close", "volume"]:
            if std_col in col_map:
                fields[std_col] = df[col_map[std_col]].astype(float)
            elif std_col.capitalize() in df.columns:
                fields[std_col] = df[std_col.capitalize()].astype(float)
            elif std_col.upper() in df.columns:
                fields[std_col] = df[std_col.upper()].astype(float)
            else:
                fields[std_col] = pd.Series(0.0, index=df.index, dtype=float)

        close = fields["close"]
        open_p = fields["open"]
        high = fields["high"]
        low = fields["low"]
        vol = fields["volume"]

        # Synthetic derived fields
        fields["returns"] = close.pct_change().fillna(0.0)
        fields["hl_spread"] = (high - low) / close.replace(0.0, 1e-6)
        fields["oc_spread"] = (close - open_p) / close.replace(0.0, 1e-6)

        # Approximate intraday / daily VWAP
        if "vwap" in col_map:
            fields["vwap"] = df[col_map["vwap"]].astype(float)
        else:
            typ_price = (high + low + close) / 3.0
            pv = typ_price * vol
            cum_vol = vol.cumsum().replace(0.0, 1e-6)
            fields["vwap"] = pv.cumsum() / cum_vol

        return fields

    def validate(self, expression: str) -> bool:
        """Validate that the expression has safe syntax and valid operator/field calls."""
        try:
            parsed = ast.parse(expression.strip(), mode="eval")
            self._validate_node(parsed.body)
            return True
        except Exception as e:
            logger.debug("Validation failed for expression '%s': %s", expression, e)
            return False

    def _validate_node(self, node: ast.AST) -> None:
        """Recursively inspect AST nodes for safety compliance."""
        if isinstance(node, ast.BinOp):
            if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)):
                raise AlphaDSLSyntaxError(f"Unsupported binary operator: {type(node.op).__name__}")
            self._validate_node(node.left)
            self._validate_node(node.right)
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.USub, ast.UAdd)):
                raise AlphaDSLSyntaxError(f"Unsupported unary operator: {type(node.op).__name__}")
            self._validate_node(node.operand)
        elif isinstance(node, ast.Compare):
            self._validate_node(node.left)
            for comparator in node.comparators:
                self._validate_node(comparator)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise AlphaDSLSyntaxError("Function calls must be named identifiers")
            func_name = node.func.id.lower()
            if func_name not in self.operators:
                raise AlphaDSLSyntaxError(f"Unknown alpha operator: '{func_name}'")
            for arg in node.args:
                self._validate_node(arg)
        elif isinstance(node, ast.Name):
            ident = node.id.lower()
            if ident not in self.SUPPORTED_FIELDS and ident not in self.operators:
                raise AlphaDSLSyntaxError(f"Unknown field or identifier: '{ident}'")
        elif isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float, bool)):
                raise AlphaDSLSyntaxError(f"Unsupported constant type: {type(node.value).__name__}")
        else:
            raise AlphaDSLSyntaxError(f"Unsupported syntax structure: {type(node).__name__}")

    def evaluate(self, expression: str, data: pd.DataFrame | dict[str, pd.Series]) -> pd.Series:
        """
        Evaluate a formulaic alpha expression against market data.
        Returns continuous alpha score series aligned with the input index.
        """
        if isinstance(data, pd.DataFrame):
            fields = self.prepare_data_fields(data)
            index = data.index
        else:
            fields = data
            index = next(iter(fields.values())).index if fields else pd.RangeIndex(0)

        parsed = ast.parse(expression.strip(), mode="eval")
        res = self._eval_node(parsed.body, fields, index)

        if isinstance(res, (int, float)):
            return pd.Series(float(res), index=index)
        if isinstance(res, pd.Series):
            return res.replace([np.inf, -np.inf], 0.0).fillna(0.0)
        return pd.Series(res, index=index).replace([np.inf, -np.inf], 0.0).fillna(0.0)

    def _eval_node(
        self,
        node: ast.AST,
        fields: dict[str, pd.Series],
        index: pd.Index,
    ) -> Any:
        """Evaluate parsed AST nodes recursively in vectorized NumPy/pandas."""
        if isinstance(node, ast.Constant):
            return node.value

        if isinstance(node, ast.Name):
            ident = node.id.lower()
            if ident in fields:
                return fields[ident]
            if ident in self.operators:
                return self.operators[ident]
            raise AlphaDSLSyntaxError(f"Unresolved identifier: {ident}")

        if isinstance(node, ast.UnaryOp):
            val = self._eval_node(node.operand, fields, index)
            if isinstance(node.op, ast.USub):
                return -val
            if isinstance(node.op, ast.UAdd):
                return val
            raise AlphaDSLSyntaxError(f"Unsupported unary operator: {type(node.op)}")

        if isinstance(node, ast.BinOp):
            left = self._eval_node(node.left, fields, index)
            right = self._eval_node(node.right, fields, index)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return safe_div(left, right)
            if isinstance(node.op, ast.Pow):
                return left**right
            if isinstance(node.op, ast.Mod):
                return left % right
            raise AlphaDSLSyntaxError(f"Unsupported binary operator: {type(node.op)}")

        if isinstance(node, ast.Compare):
            left = self._eval_node(node.left, fields, index)
            # Evaluate pairwise comparisons
            res = pd.Series(True, index=index) if isinstance(left, pd.Series) else True
            curr_left = left
            for op, comparator in zip(node.ops, node.comparators, strict=False):
                curr_right = self._eval_node(comparator, fields, index)
                if isinstance(op, ast.Gt):
                    step = curr_left > curr_right
                elif isinstance(op, ast.Lt):
                    step = curr_left < curr_right
                elif isinstance(op, ast.GtE):
                    step = curr_left >= curr_right
                elif isinstance(op, ast.LtE):
                    step = curr_left <= curr_right
                elif isinstance(op, ast.Eq):
                    step = curr_left == curr_right
                elif isinstance(op, ast.NotEq):
                    step = curr_left != curr_right
                else:
                    raise AlphaDSLSyntaxError(f"Unsupported comparison operator: {type(op)}")
                res = res & step if isinstance(res, pd.Series) else (res and step)
                curr_left = curr_right
            return res.astype(float) if isinstance(res, pd.Series) else float(res)

        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise AlphaDSLSyntaxError("Function call target must be a named operator")
            op_name = node.func.id.lower()
            if op_name not in self.operators:
                raise AlphaDSLSyntaxError(f"Operator '{op_name}' not available")
            op_fn: Any = self.operators[op_name]
            evaluated_args = [self._eval_node(arg, fields, index) for arg in node.args]
            return op_fn(*evaluated_args)

        raise AlphaDSLSyntaxError(f"Unhandled AST node: {type(node).__name__}")
