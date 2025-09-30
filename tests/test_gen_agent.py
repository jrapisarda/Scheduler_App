# generate_pytest_agent.py
"""
AI Agent: generate pytest smoke tests for the most important parts of a codebase.

What it does
------------
- Walks the repo and parses .py files with `ast`.
- Scores functions and classes by:
  importance ~= 0.7*loc + 0.3*(cyclomatic_complexity*10) + filename/topic bonus
- Picks the top-N items and generates tests:
  - Module import test
  - Class instantiation test (no-arg or skips if ctor non-trivial)
  - Function/method call test with best-effort dummy args based on type hints/defaults
  - Wraps unknown/side-effecty calls in try/except and marks xfail or skip
- Writes to tests/test_autogen_smoke.py (creates tests/ if needed)

Usage
-----
python generate_pytest_agent.py --root . --top-n 25 --outfile tests/test_autogen_smoke.py

Notes
-----
- This is a *smoke* test generator. It asserts "does not crash to import/call".
- You can hand-edit the generated file to add real assertions.
"""

from __future__ import annotations
import argparse
import ast
import os
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Dict

IMPORTANT_FILENAME_BONUS = {
    "app": 15,
    "main": 10,
    "engine": 12,
    "scheduler": 12,
    "service": 8,
    "api": 8,
    "worker": 6,
}

EXCLUDE_DIRS = {"venv", ".venv", "__pycache__", ".git", "node_modules", "migrations", "env", ".env", "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "site-packages", "data"}

@dataclass
class Item:
    kind: str  # "function" or "class" or "method"
    module: str  # dot-path module name
    relpath: str  # file relative path
    name: str
    qualname: str  # e.g. ClassName.method or func
    lineno: int
    end_lineno: int
    loc: int
    complexity: int
    score: float
    argspec: Optional[List[Tuple[str, Optional[str], bool, bool]]] = field(default=None)
    # argspec entries: (arg_name, type_hint, has_default, is_vararg/kw)

def iter_py_files(root: Path):
    for p in root.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        # ignore tests we generate or existing tests
        if "tests" in p.parts and p.name.startswith("test_"):
            continue
        yield p

def module_name_from_path(root: Path, file_path: Path) -> str:
    rel = file_path.relative_to(root).with_suffix("")
    parts = []
    for part in rel.parts:
        if part == "__init__":
            continue
        parts.append(part)
    return ".".join(parts)

def cyclomatic_complexity(node: ast.AST) -> int:
    # very rough CC: count decision points
    decision_nodes = (
        ast.If, ast.For, ast.While, ast.Try, ast.With,
        ast.BoolOp, ast.IfExp, ast.Match
    )
    comp_nodes = (ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp)
    count = 1
    for n in ast.walk(node):
        if isinstance(n, decision_nodes) or isinstance(n, comp_nodes):
            count += 1
    return count

def get_end_lineno(node: ast.AST) -> int:
    return getattr(node, "end_lineno", getattr(node, "lineno", 0))

def filename_bonus(path: Path) -> int:
    base = path.stem.lower()
    bonus = 0
    for key, val in IMPORTANT_FILENAME_BONUS.items():
        if key in base:
            bonus += val
    return bonus

def extract_type_str(ann: Optional[ast.AST]) -> Optional[str]:
    if ann is None:
        return None
    try:
        # best-effort pretty printing of annotation
        return ast.unparse(ann)  # Python 3.9+
    except Exception:
        return None

def argspec_for_function(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> List[Tuple[str, Optional[str], bool, bool]]:
    spec = []
    defaults = fn.args.defaults or []
    kw_defaults = fn.args.kw_defaults or []
    pos_args = fn.args.args
    # map last N positional args to defaults
    default_start = len(pos_args) - len(defaults)

    for idx, a in enumerate(pos_args):
        has_default = idx >= default_start
        spec.append((a.arg, extract_type_str(a.annotation), has_default, False))
    if fn.args.vararg:
        spec.append((fn.args.vararg.arg, None, True, True))
    for a, d in zip(fn.args.kwonlyargs, kw_defaults):
        has_default = d is not None
        spec.append((a.arg, extract_type_str(a.annotation), has_default, False))
    if fn.args.kwarg:
        spec.append((fn.args.kwarg.arg, None, True, True))
    # drop self/cls
    spec = [s for s in spec if s[0] not in ("self", "cls")]
    return spec

def analyze_file(root: Path, file_path: Path) -> List[Item]:
    items: List[Item] = []
    try:
        src = file_path.read_text(encoding="utf-8")
    except Exception:
        return items
    try:
        tree = ast.parse(src, filename=str(file_path))
    except SyntaxError:
        return items

    mod_name = module_name_from_path(root, file_path)
    fbonus = filename_bonus(file_path)

    for node in tree.body:
        # top-level functions
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            loc = max(1, get_end_lineno(node) - node.lineno + 1)
            cc = cyclomatic_complexity(node)
            score = 0.7 * loc + 0.3 * (cc * 10) + fbonus
            items.append(
                Item(
                    kind="function",
                    module=mod_name,
                    relpath=str(file_path.relative_to(root)),
                    name=node.name,
                    qualname=node.name,
                    lineno=node.lineno,
                    end_lineno=get_end_lineno(node),
                    loc=loc,
                    complexity=cc,
                    score=score,
                    argspec=argspec_for_function(node),
                )
            )
        # classes and methods
        if isinstance(node, ast.ClassDef):
            # class score by body span
            loc = max(1, get_end_lineno(node) - node.lineno + 1)
            cc = cyclomatic_complexity(node)
            score = 0.7 * loc + 0.3 * (cc * 10) + fbonus + 5  # slight bonus for classes
            items.append(
                Item(
                    kind="class",
                    module=mod_name,
                    relpath=str(file_path.relative_to(root)),
                    name=node.name,
                    qualname=node.name,
                    lineno=node.lineno,
                    end_lineno=get_end_lineno(node),
                    loc=loc,
                    complexity=cc,
                    score=score,
                )
            )
            # methods
            for b in node.body:
                if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)) and not b.name.startswith("_"):
                    locm = max(1, get_end_lineno(b) - b.lineno + 1)
                    ccm = cyclomatic_complexity(b)
                    scorem = 0.7 * locm + 0.3 * (ccm * 10) + fbonus + 3
                    items.append(
                        Item(
                            kind="method",
                            module=mod_name,
                            relpath=str(file_path.relative_to(root)),
                            name=b.name,
                            qualname=f"{node.name}.{b.name}",
                            lineno=b.lineno,
                            end_lineno=get_end_lineno(b),
                            loc=locm,
                            complexity=ccm,
                            score=scorem,
                            argspec=argspec_for_function(b),
                        )
                    )
    return items

# ---- Test code generation ----------------------------------------------------

DUMMY_VALUES_BY_ANNOTATION = {
    "int": "0",
    "float": "0.0",
    "str": "''",
    "bool": "False",
    "list": "[]",
    "dict": "{}",
    "typing.List": "[]",
    "typing.Dict": "{}",
    "typing.Optional[int]": "0",
    "typing.Optional[float]": "0.0",
    "typing.Optional[str]": "''",
    "typing.Optional[bool]": "False",
}

def dummy_arg_for(type_hint: Optional[str], has_default: bool, is_vararg: bool) -> Optional[str]:
    if is_vararg:
        return None  # we won't try to pass *args/**kwargs in auto mode
    if type_hint:
        t = type_hint.replace(" ", "")
        # strip Optional[...] to inner if possible
        if t.startswith("Optional[") and t.endswith("]"):
            inner = t[len("Optional["):-1]
            return DUMMY_VALUES_BY_ANNOTATION.get(inner, "None")
        return DUMMY_VALUES_BY_ANNOTATION.get(t, "None" if has_default else None)
    return "None" if has_default else None

def render_test_header(repo_root: str) -> str:
    return textwrap.dedent(f"""
    # AUTOGENERATED BY generate_pytest_agent.py
    import os, sys, importlib, inspect, pytest

    # Ensure repo root on sys.path for module imports
    REPO_ROOT = os.path.abspath({repo_root!r})
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)

    """)

def render_module_import_test(module: str) -> str:
    safe_name = module.replace(".", "_")
    return textwrap.dedent(f"""
    def test_import_module__{safe_name}():
        try:
            m = importlib.import_module({module!r})
            assert m is not None
        except Exception as e:
            pytest.fail(f"Failed to import {{ {module!r} }}: {{e}}")
    """)

def render_class_test(module: str, class_name: str) -> str:
    safe = f"{module.replace('.', '_')}__{class_name}"
    return textwrap.dedent(f"""
    def test_class_instantiation__{safe}():
        m = importlib.import_module({module!r})
        cls = getattr(m, {class_name!r}, None)
        assert cls is not None, "Class {class_name} not found in {module}"
        try:
            obj = cls()  # best-effort no-arg
        except TypeError:
            pytest.skip("Constructor for {class_name} requires args; edit test to provide fixtures.")
        except Exception as e:
            pytest.xfail(f"Instantiation raised {{e}}; manual review needed.")
    """)

def render_function_call_test(module: str, qualname: str, argspec: Optional[List[Tuple[str, Optional[str], bool, bool]]]) -> str:
    safe = f"{module.replace('.', '_')}__{qualname.replace('.', '__')}"
    # Build arg list
    call_args: List[str] = []
    need_skip = False
    if argspec:
        for name, ann, has_default, is_var in argspec:
            val = dummy_arg_for(ann, has_default, is_var)
            if val is None:
                need_skip = True
            else:
                call_args.append(f"{val}")
    call_args_str = ", ".join(call_args)
    skip_line = "pytest.skip('No safe dummy args; edit test to supply fixtures.')" if need_skip and not call_args else ""
    target_expr = qualname  # "func" or "Class.method" accessed via getattr chain

    # Build getattr chain for methods
    if "." in qualname:
        cls, meth = qualname.split(".", 1)
        body = f"""
        m = importlib.import_module({module!r})
        cls = getattr(m, {cls!r}, None)
        assert cls is not None, "Class {cls} not found in {module}"
        try:
            obj = cls()
        except TypeError:
            pytest.skip("Constructor for {cls} requires args; edit test to provide fixtures.")
        fn = getattr(obj, {meth!r}, None)
        assert callable(fn), "Method {meth} not found or not callable"
        """
        call = f"fn({call_args_str})" if call_args_str else "fn()"
    else:
        body = f"""
        m = importlib.import_module({module!r})
        fn = getattr(m, {qualname!r}, None)
        assert callable(fn), "Function {qualname} not found or not callable"
        """
        call = f"fn({call_args_str})" if call_args_str else "fn()"

    return textwrap.dedent(f"""
    def test_call__{safe}():
        {"pass" if not (body or call) else body}
        {";" if body.strip().endswith(":") else ""}
        {"# decide to skip if no safe args" if skip_line else ""}
        {skip_line}
        try:
            _ = {call}
        except Exception as e:
            pytest.xfail(f"Auto-call raised {{e}}; requires human-provided fixtures.")
    """)

def generate_tests(root: Path, items: List[Item], top_n: int, outfile: Path):
    # sort and take top N, but ensure we include at least one import test per module selected
    items_sorted = sorted(items, key=lambda x: x.score, reverse=True)[:top_n]
    modules = sorted({it.module for it in items_sorted})

    lines: List[str] = []
    lines.append(render_test_header(str(root)))
    # Module imports first
    for mod in modules:
        lines.append(render_module_import_test(mod))

    # Then class instantiation tests for high-scoring classes
    for it in items_sorted:
        if it.kind == "class":
            lines.append(render_class_test(it.module, it.name))

    # Then function/method calls
    for it in items_sorted:
        if it.kind in ("function", "method"):
            lines.append(render_function_call_test(it.module, it.qualname, it.argspec))

    outfile.parent.mkdir(parents=True, exist_ok=True)
    outfile.write_text("\n".join(lines), encoding="utf-8")

def main():
    ap = argparse.ArgumentParser(description="Generate pytest smoke tests for important code.")
    ap.add_argument("--root", type=str, default=".", help="Repo root directory")
    ap.add_argument("--top-n", type=int, default=25, help="Number of top items to test")
    ap.add_argument("--outfile", type=str, default="tests/test_autogen_smoke.py", help="Output pytest file")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        print(f"Root {root} not found", file=sys.stderr)
        sys.exit(1)

    all_items: List[Item] = []
    for f in iter_py_files(root):
        all_items.extend(analyze_file(root, f))

    if not all_items:
        print("No Python items found to analyze.")
        sys.exit(2)

    generate_tests(root, all_items, args.top_n, Path(args.outfile))
    print(f"✅ Generated {args.outfile} with {min(args.top_n, len(all_items))} targets across {len({i.module for i in all_items})} modules.")
    print("Run with: pytest -q")

if __name__ == "__main__":
    main()
