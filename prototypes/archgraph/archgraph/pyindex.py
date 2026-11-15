"""Deterministic Python indexing: symbols (functions, classes, methods) and import edges.

Only structure is recorded: names, line ranges, and hashes of normalised AST. Source text is
not stored. Docstrings are excluded from hashes so that documentation-only edits are not
reported as code changes.
"""

import ast
import hashlib
from dataclasses import dataclass

FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Symbol:
    path: str
    qualname: str
    kind: str
    start: int
    end: int
    signature_hash: str
    body_hash: str
    tokens: tuple

    @property
    def key(self):
        return f"{self.path}::{self.qualname}"


def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _strip_docstring(body):
    first = body[0] if body else None
    if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant):
        if isinstance(first.value.value, str):
            return body[1:]
    return body


def _body_hash(statements):
    return _digest(ast.dump(ast.Module(body=statements, type_ignores=[]), include_attributes=False))


def _tokens(statements):
    names = set()
    for statement in statements:
        for node in ast.walk(statement):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and len(node.value) <= 40
            ):
                names.add(node.value)
    return tuple(sorted(names))


def _function_symbol(path, qualname, kind, node):
    body = _strip_docstring(node.body)
    return Symbol(
        path=path,
        qualname=qualname,
        kind=kind,
        start=node.lineno,
        end=node.end_lineno or node.lineno,
        signature_hash=_digest(ast.dump(node.args) + node.name),
        body_hash=_body_hash(body),
        tokens=_tokens(body),
    )


def index_python(path, data):
    """Return {"symbols": [Symbol], "imports": [(level, module)], "parse_error": str | None}."""
    try:
        tree = ast.parse(data.decode("utf-8"), filename=path)
    except (SyntaxError, UnicodeDecodeError) as exc:
        return {"symbols": [], "imports": [], "parse_error": type(exc).__name__}

    symbols = []
    for node in tree.body:
        if isinstance(node, FUNCTIONS):
            symbols.append(_function_symbol(path, node.name, "function", node))
        elif isinstance(node, ast.ClassDef):
            attributes = [
                s
                for s in _strip_docstring(node.body)
                if not isinstance(s, (*FUNCTIONS, ast.ClassDef))
            ]
            symbols.append(
                Symbol(
                    path=path,
                    qualname=node.name,
                    kind="class",
                    start=node.lineno,
                    end=node.end_lineno or node.lineno,
                    signature_hash=_digest(ast.dump(ast.Tuple(elts=node.bases, ctx=ast.Load()))),
                    body_hash=_body_hash(attributes),
                    tokens=_tokens(attributes),
                )
            )
            for child in node.body:
                if isinstance(child, FUNCTIONS):
                    symbols.append(
                        _function_symbol(path, f"{node.name}.{child.name}", "method", child)
                    )

    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add((0, alias.name))
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.level or 0, node.module or ""))
    return {"symbols": symbols, "imports": sorted(imports), "parse_error": None}
