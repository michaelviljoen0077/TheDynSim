"""Static validation gate: no plugin executes a single line before passing here.

Engine-owned so the refusal is structural (FR7): PluginHost will not install
unvalidated source. Every failure is machine-readable — the governor's repair
round-trip and the lab notebook consume these reasons verbatim (FR9, Story 2.2).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

CONTRACT_VERSION = 1
ALLOWED_IMPORTS = {"math", "typing"}
SPECIES_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")

BANNED_CALLS = {
    "eval", "exec", "compile", "__import__", "open", "input", "print",
    "getattr", "setattr", "delattr", "globals", "locals", "vars", "exit", "quit",
    "breakpoint", "memoryview", "super",
}
BANNED_NAMES = {"random", "numpy", "np", "os", "sys", "socket", "subprocess"}
META_REQUIRED = {"name", "contract", "species"}


@dataclass
class Violation:
    code: str
    message: str
    line: int = 0

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "line": self.line}


@dataclass
class ValidationResult:
    ok: bool
    errors: list[Violation] = field(default_factory=list)
    warnings: list[Violation] = field(default_factory=list)
    meta: dict | None = None

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": [v.as_dict() for v in self.errors],
            "warnings": [v.as_dict() for v in self.warnings],
            "meta": self.meta,
        }


def validate_plugin(source: str) -> ValidationResult:
    errors: list[Violation] = []
    warnings: list[Violation] = []

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return ValidationResult(False, [Violation("syntax", f"syntax error: {e.msg}", e.lineno or 0)])

    meta = _check_module_top_level(tree, errors)
    _check_meta(meta, errors)
    _check_contract_functions(tree, errors)
    _walk_banned_constructs(tree, errors, warnings)

    return ValidationResult(not errors, errors, warnings, meta)


def _check_module_top_level(tree: ast.Module, errors: list[Violation]) -> dict | None:
    """Module top level: allowlisted imports, PLUGIN_META, defs, one docstring. Nothing else.

    This is what keeps plugin state snapshot-complete — module-level mutable
    state would silently desynchronize from every restored snapshot.
    """
    meta: dict | None = None
    for i, stmt in enumerate(tree.body):
        if isinstance(stmt, ast.Expr) and i == 0 and isinstance(stmt.value, ast.Constant) \
                and isinstance(stmt.value.value, str):
            continue  # module docstring
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_IMPORTS:
                    errors.append(Violation("banned-import", f"import of {alias.name!r} not in allowlist {sorted(ALLOWED_IMPORTS)}", stmt.lineno))
            continue
        if isinstance(stmt, ast.ImportFrom):
            root = (stmt.module or "").split(".")[0]
            if root not in ALLOWED_IMPORTS:
                errors.append(Violation("banned-import", f"from-import of {stmt.module!r} not in allowlist {sorted(ALLOWED_IMPORTS)}", stmt.lineno))
            if any(a.name == "*" for a in stmt.names):
                errors.append(Violation("banned-import", "wildcard imports are not allowed", stmt.lineno))
            continue
        if isinstance(stmt, ast.FunctionDef):
            continue
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                and isinstance(stmt.targets[0], ast.Name) and stmt.targets[0].id == "PLUGIN_META":
            if meta is not None:
                errors.append(Violation("meta-duplicate", "PLUGIN_META assigned more than once", stmt.lineno))
                continue
            try:
                value = ast.literal_eval(stmt.value)
            except (ValueError, SyntaxError):
                errors.append(Violation("meta-not-literal", "PLUGIN_META must be a literal dict (no expressions)", stmt.lineno))
                continue
            if not isinstance(value, dict):
                errors.append(Violation("meta-not-dict", "PLUGIN_META must be a dict", stmt.lineno))
                continue
            meta = value
            continue
        errors.append(Violation(
            "module-state",
            "module top level may contain only allowlisted imports, PLUGIN_META, and function "
            "definitions — plugin state must live in entity props or world.store "
            f"(found {type(stmt).__name__})",
            stmt.lineno,
        ))
    return meta


def _check_meta(meta: dict | None, errors: list[Violation]) -> None:
    if meta is None:
        errors.append(Violation("meta-missing", "PLUGIN_META is missing"))
        return
    missing = META_REQUIRED - meta.keys()
    if missing:
        errors.append(Violation("meta-incomplete", f"PLUGIN_META missing keys: {sorted(missing)}"))
        return
    if meta.get("contract") != CONTRACT_VERSION:
        errors.append(Violation("contract-version", f"unknown contract version {meta.get('contract')!r}; engine supports {CONTRACT_VERSION}"))
    if not isinstance(meta.get("name"), str) or not SPECIES_NAME_RE.match(str(meta.get("name"))):
        errors.append(Violation("meta-name", "PLUGIN_META['name'] must match ^[a-z][a-z0-9_]{0,30}$"))
    species = meta.get("species")
    if not isinstance(species, list) or not species:
        errors.append(Violation("meta-species", "PLUGIN_META['species'] must be a non-empty list of species names"))
    else:
        for s in species:
            if not isinstance(s, str) or not SPECIES_NAME_RE.match(s):
                errors.append(Violation("species-name", f"illegal species name {s!r} (must match ^[a-z][a-z0-9_]{{0,30}}$)"))
    parent = meta.get("lineage_parent")
    if parent is not None and not isinstance(parent, str):
        errors.append(Violation("meta-lineage", "PLUGIN_META['lineage_parent'] must be a string or None"))


def _check_contract_functions(tree: ast.Module, errors: list[Violation]) -> None:
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for name in ("setup", "on_tick"):
        fn = fns.get(name)
        if fn is None:
            errors.append(Violation("contract-missing", f"required function {name}(world) is missing"))
            continue
        args = fn.args
        n_args = len(args.args) + len(args.posonlyargs)
        if n_args != 1 or args.vararg or args.kwarg or args.kwonlyargs:
            errors.append(Violation("contract-signature", f"{name} must take exactly one argument (world)", fn.lineno))


def _walk_banned_constructs(tree: ast.Module, errors: list[Violation],
                            warnings: list[Violation]) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in BANNED_CALLS:
            errors.append(Violation("banned-call", f"call to {node.func.id!r} is not allowed", node.lineno))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) \
                and node.id in BANNED_NAMES:
            errors.append(Violation("banned-name", f"use of {node.id!r} is not allowed (randomness must come from world.rng)", node.lineno))
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            errors.append(Violation("dunder-access", f"dunder attribute access ({node.attr!r}) is not allowed", node.lineno))
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            errors.append(Violation("global-state", f"{type(node).__name__.lower()} statements are not allowed", node.lineno))
        elif isinstance(node, (ast.Import, ast.ImportFrom)) and node.col_offset > 0:
            errors.append(Violation("nested-import", "imports inside functions are not allowed", node.lineno))
        elif isinstance(node, ast.While):
            if isinstance(node.test, ast.Constant) and node.test.value is True \
                    and not _has_break(node):
                warnings.append(Violation("unbounded-loop", "while True without break — shadow budgets will kill this", node.lineno))
        elif isinstance(node, ast.ClassDef):
            errors.append(Violation("class-def", "class definitions are not allowed in plugins", node.lineno))


def _has_break(loop: ast.While) -> bool:
    for node in ast.walk(loop):
        if isinstance(node, ast.Break):
            return True
    return False
