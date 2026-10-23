"""Flow model helpers: safe expressions, iteration and static validation."""

from __future__ import annotations

import ast
import operator
from typing import Any, Callable, Iterator

from ..config.models import FlowNode, TestProgram
from .context import SiteContext

__all__ = ["ExpressionError", "SafeExpression", "iter_flow_items", "validate_flow"]


class ExpressionError(ValueError):
    """Raised when a flow expression is invalid or uses a forbidden construct."""


_BIN_OPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
    ast.Not: operator.not_,
}
_COMPARE_OPS: dict[type[ast.cmpop], Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}
_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.BinOp, ast.UnaryOp, ast.Compare,
    ast.Call, ast.Name, ast.Constant, ast.Load, ast.Tuple, ast.List, ast.keyword,
    *_BIN_OPS.keys(), *_UNARY_OPS.keys(), *_COMPARE_OPS.keys(),
)
_ALLOWED_FUNCS = {
    "abs": abs, "min": min, "max": max, "round": round, "len": len,
    "int": int, "float": float, "bool": bool, "str": str,
}


class SafeExpression:
    """A tiny expression evaluator with an explicit AST whitelist.

    Only arithmetic/comparison, boolean logic and calls to a fixed function
    set are allowed.  Attribute access, imports, subscriptions and
    comprehensions are rejected, so ``__import__`` and friends cannot run.
    """

    def __init__(self, source: str) -> None:
        self.source = source
        try:
            tree = ast.parse(source, mode="eval")
        except SyntaxError as exc:
            raise ExpressionError(f"invalid expression '{source}': {exc.msg}") from exc
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED_NODES):
                raise ExpressionError(
                    f"expression '{source}' uses forbidden construct {type(node).__name__}"
                )
            if isinstance(node, ast.Name) and node.id.startswith("_"):
                raise ExpressionError(f"expression '{source}' uses private name '{node.id}'")
        self._tree = tree

    def evaluate(self, ctx: SiteContext, extra: dict[str, Any] | None = None) -> Any:
        """Evaluate against ``ctx`` (results/vars) plus an optional dict."""
        names = {"__builtins__": {}, **_ALLOWED_FUNCS}

        def _result(name: str) -> float:
            value = ctx.value_of(name)
            return 0.0 if value is None else float(value)

        def _passed(name: str) -> bool:
            result = ctx.result_of(name)
            return bool(result.passed) if result is not None else False

        def _var(name: str, default: Any = None) -> Any:
            if name in ctx.vars:
                return ctx.vars[name]
            return (extra or {}).get(name, default)

        def _get(name: str, default: Any = None) -> Any:
            return _var(name, default)

        names.update({"result": _result, "value": _result, "passed": _passed,
                      "var": _var, "get": _get})
        try:
            return self._eval(self._tree.body, names)
        except ExpressionError:
            raise
        except Exception as exc:  # noqa: BLE001 - runtime errors become ExpressionError
            raise ExpressionError(f"cannot evaluate '{self.source}': {exc}") from exc

    def __call__(self, ctx: SiteContext, extra: dict[str, Any] | None = None) -> Any:
        return self.evaluate(ctx, extra)

    def _eval(self, node: ast.AST, names: dict[str, Any]) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise ExpressionError(f"unknown name '{node.id}'")
            return names[node.id]
        if isinstance(node, ast.Tuple):
            return tuple(self._eval(item, names) for item in node.elts)
        if isinstance(node, ast.List):
            return [self._eval(item, names) for item in node.elts]
        if isinstance(node, ast.BinOp):
            func = _BIN_OPS.get(type(node.op))
            if func is None:
                raise ExpressionError(f"unsupported operator {type(node.op).__name__}")
            return func(self._eval(node.left, names), self._eval(node.right, names))
        if isinstance(node, ast.UnaryOp):
            func_u = _UNARY_OPS.get(type(node.op))
            if func_u is None:
                raise ExpressionError(f"unsupported unary operator {type(node.op).__name__}")
            return func_u(self._eval(node.operand, names))
        if isinstance(node, ast.BoolOp):
            values = [self._eval(item, names) for item in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare):
            left = self._eval(node.left, names)
            for op, comparator in zip(node.ops, node.comparators):
                func_c = _COMPARE_OPS.get(type(op))
                if func_c is None:
                    raise ExpressionError(f"unsupported comparison {type(op).__name__}")
                right = self._eval(comparator, names)
                if not func_c(left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ExpressionError("only plain function calls are allowed")
            func_name = node.func.id
            if func_name in _ALLOWED_FUNCS:
                args = [self._eval(arg, names) for arg in node.args]
                return _ALLOWED_FUNCS[func_name](*args)
            target = names.get(func_name)
            if not callable(target) or func_name in _ALLOWED_FUNCS:
                raise ExpressionError(f"call to unknown function '{func_name}'")
            args = [self._eval(arg, names) for arg in node.args]
            kwargs = {kw.arg: self._eval(kw.value, names) for kw in node.keywords if kw.arg}
            return target(*args, **kwargs)
        raise ExpressionError(f"unsupported node {type(node).__name__}")


def iter_flow_items(nodes: list[FlowNode]) -> Iterator[FlowNode]:
    """Depth-first iteration over every item node."""
    for node in nodes:
        if node.item is not None:
            yield node
        if node.items:
            yield from iter_flow_items(node.items)
        if node.branch is not None:
            yield from iter_flow_items(node.branch.then)
            yield from iter_flow_items(node.branch.else_)
        if node.loop is not None:
            yield from iter_flow_items(node.loop.items)


def validate_flow(program: TestProgram, *, stage: str, limits_known: set[int] | None = None,
                  item_registry: Callable[[str], bool] | None = None) -> list[str]:
    """Static checks for one flow; returns a list of error strings."""
    errors: list[str] = []
    if stage not in program.flows:
        return [f"stage '{stage}' not defined (available: {', '.join(sorted(program.flows))})"]
    seen_numbers: dict[int, str] = {}
    for node in iter_flow_items(program.flows[stage]):
        name = str(node.item)
        if item_registry is not None and not item_registry(name):
            errors.append(f"unknown test item '{name}' (test_num {node.test_num})")
        number = int(node.test_num or 0)
        if number in seen_numbers:
            errors.append(
                f"test_num {number} used twice ('{seen_numbers[number]}' and '{name}')"
            )
        seen_numbers[number] = name
        if node.on_fail is not None and node.on_fail.action == "retest" and node.on_fail.max_retries < 1:
            errors.append(f"item '{name}': retest action needs max_retries >= 1")
        if node.branch is not None:
            try:
                SafeExpression(node.branch.if_)
            except ExpressionError as exc:
                errors.append(f"branch condition rejected: {exc}")
    if limits_known is not None:
        for number, name in seen_numbers.items():
            if number not in limits_known:
                errors.append(
                    f"no limit row for test_num {number} ('{name}') - item defaults will be used"
                )
    return errors


def evaluate_branch(expression: str, ctx: SiteContext, extra: dict[str, Any] | None = None) -> bool:
    """Evaluate a branch condition."""
    return bool(SafeExpression(expression).evaluate(ctx, extra))
