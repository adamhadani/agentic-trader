"""Bounded typed AST evolution with an ask/tell quality-diversity archive.

Fitness is supplied by the validation pipeline. This module never sees prices,
labels or holdout observations. Mutation/crossover must pass the same DSL compiler.
"""

from __future__ import annotations

import ast
import copy
import random

from agentic_trader.research.alpha.dsl import AlphaDSLSyntaxError, compile_expression


SEED_EXPRESSIONS = (
    "roc(close,5)",
    "ts_residual(close,20)",
    "-delta(close,3)*ts_rank(volume,10)",
    "ts_corr(close,volume,10)",
    "ts_slope(close,20)",
    "ts_rank(volume,10)",
)
WINDOWS = (3, 5, 8, 10, 14, 20, 30, 60)


def canonical_expression(expression):
    compile_expression(expression)
    return ast.unparse(ast.parse(expression, mode="eval"))


class TypedGeneticSearch:
    def __init__(self, seed: int, archive_size: int = 32):
        self.rng = random.Random(seed)
        self.archive_size = archive_size
        self.archive: dict[str, float] = {}
        self.seen: set[str] = set()
        self.crossover_count = 0
        self.mutation_count = 0

    def tell(self, expression: str, fitness: float):
        expression = canonical_expression(expression)
        self.seen.add(expression)
        self.archive[expression] = fitness
        # Retain leaders across structural families before filling by quality.
        ordered = sorted(self.archive, key=lambda e: (-self.archive[e], len(e), e))
        retained = []
        families = set()
        for expr in ordered:
            family = tuple(
                sorted(
                    {
                        n.func.id
                        for n in ast.walk(ast.parse(expr, mode="eval"))
                        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    }
                )
            )
            if family not in families:
                retained.append(expr)
                families.add(family)
        retained = (retained + [e for e in ordered if e not in retained])[: self.archive_size]
        self.archive = {e: self.archive[e] for e in retained}

    def crossover(self, left: str, right: str):
        left_tree = ast.parse(left, mode="eval")
        right_tree = ast.parse(right, mode="eval")
        donors = [n for n in ast.walk(right_tree) if isinstance(n, (ast.Call, ast.BinOp, ast.Name))]
        for _ in range(30):
            tree = copy.deepcopy(left_tree)
            targets = [n for n in ast.walk(tree) if isinstance(n, (ast.Call, ast.BinOp, ast.Name))]
            target = self.rng.choice(targets)
            donor = copy.deepcopy(self.rng.choice(donors))

            class Substitute(ast.NodeTransformer):
                def __init__(self, original, replacement):
                    self.original, self.replacement = original, replacement

                def visit(self, node):
                    return self.replacement if node is self.original else super().visit(node)

            proposed = ast.unparse(Substitute(target, donor).visit(tree))
            try:
                canonical = canonical_expression(proposed)
            except AlphaDSLSyntaxError:
                continue
            if canonical not in (canonical_expression(left), canonical_expression(right)):
                self.crossover_count += 1
                return canonical
        return self.mutate(left)

    def mutate(self, expression):
        for _ in range(30):
            tree = ast.parse(expression, mode="eval")
            windows = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and type(node.value) is int and node.value > 1
            ]
            if windows and self.rng.random() < 0.6:
                self.rng.choice(windows).value = self.rng.choice(WINDOWS)
                proposed = ast.unparse(tree)
            else:
                op = self.rng.choice(("ts_mean", "delta", "ts_rank", "ts_residual", "decay_linear"))
                proposed = f"{op}({expression},{self.rng.choice(WINDOWS)})"
            try:
                canonical = canonical_expression(proposed)
            except AlphaDSLSyntaxError:
                continue
            self.mutation_count += 1
            return canonical
        return self.rng.choice(SEED_EXPRESSIONS)

    def ask(self):
        for _ in range(100):
            parents = sorted(self.archive) or list(SEED_EXPRESSIONS)
            if len(parents) > 1 and self.rng.random() < 0.5:
                left, right = self.rng.sample(parents, 2)
                proposed = self.crossover(left, right)
            else:
                proposed = self.mutate(self.rng.choice(parents))
            if proposed not in self.seen:
                self.seen.add(proposed)
                return proposed
        raise ValueError("Unique expression budget exhausted")
