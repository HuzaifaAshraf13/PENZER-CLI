"""Plugin discovery and lightweight extension hooks for PENZER-CLI."""

import importlib
import inspect
import json
import os
import ast
import subprocess
import sys
import shutil
from pathlib import Path

_PLUGIN_DIR = Path(__file__).parent
_REGISTRY_PATH = Path(".penzer") / "plugin_registry.json"

_ALLOWED_PLUGIN_IMPORTS = {"json", "math", "re", "statistics", "datetime"}
_BLOCKED_PLUGIN_NAMES = {
    "os", "sys", "subprocess", "socket", "shutil", "pathlib", "requests",
    "httpx", "eval", "exec", "compile", "open", "__import__", "input",
    "__builtins__", "getattr", "globals", "locals", "vars", "object", "type", "dir",
}


class SandboxedPlugin:
    """Proxy that executes one validated plugin call in a child process."""

    def __init__(self, module_name: str, function_name: str, description: str = "") -> None:
        self.module_name = module_name
        self.function_name = function_name
        self.__doc__ = description
        self.__name__ = function_name

    def __call__(self, **kwargs):
        payload = json.dumps(kwargs)
        runner = Path(__file__).with_name("runner.py")
        project_root = _PLUGIN_DIR.parent.parent.resolve()
        if shutil.which("bwrap"):
            command = [
                "bwrap", "--die-with-parent", "--unshare-all",
                "--ro-bind", str(project_root), str(project_root),
                "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin",
                "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
                "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
                "--chdir", str(project_root), sys.executable, str(runner),
                self.module_name, self.function_name,
            ]
        else:
            command = [sys.executable, str(runner), self.module_name, self.function_name]
        try:
            result = subprocess.run(
                command,
                input=payload,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(project_root),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("Plugin exceeded its 30 second sandbox timeout") from exc
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Sandboxed plugin failed")
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Sandboxed plugin returned invalid output") from exc
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "Sandboxed plugin failed"))
        return response.get("result", "")


def _plugin_functions(path: Path) -> list[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        (node.name, ast.get_docstring(node) or "")
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def validate_plugin_source(source: str) -> None:
    """Reject generated code that can escape the plugin's narrow contract."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"Plugin code is invalid python: {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports = [alias.name.split(".", 1)[0] for alias in node.names]
            if any(name not in _ALLOWED_PLUGIN_IMPORTS for name in imports):
                raise ValueError("Plugin imports are restricted to standard data-processing modules")
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".", 1)[0]
            if module not in _ALLOWED_PLUGIN_IMPORTS:
                raise ValueError("Plugin imports are restricted to standard data-processing modules")
        elif isinstance(node, ast.Name) and node.id in _BLOCKED_PLUGIN_NAMES:
            raise ValueError(f"Plugin uses blocked capability: {node.id.strip()}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError("Plugin uses blocked dunder access")
        elif isinstance(node, (ast.Global, ast.Nonlocal, ast.Lambda)):
            raise ValueError("Plugin contains a blocked dynamic construct")
    for index, statement in enumerate(tree.body):
        is_docstring = (
            index == 0
            and isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        )
        if not is_docstring and not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            raise ValueError("Plugin top level may only contain a docstring and function or class definitions")


def discover_plugins():
    """Discover validated plugin paths without importing generated code."""
    plugins = []
    for path in sorted(_PLUGIN_DIR.glob("*.py")):
        if path.name.startswith("_") or path.name == "__init__.py":
            continue
        try:
            validate_plugin_source(path.read_text(encoding="utf-8"))
            plugins.append(path)
        except Exception:
            continue
    return plugins


def load_plugin_tools():
    """Return tool callables defined by discovered plugins."""
    tools = {}
    for path in discover_plugins():
        for function_name, description in _plugin_functions(path):
            tools[function_name] = SandboxedPlugin(path.stem, function_name, description)
    return tools


def list_plugin_metadata():
    """Return lightweight metadata for discovered plugins for CLI inspection."""
    metadata = []
    for path in sorted(_PLUGIN_DIR.glob("*.py")):
        if path.name.startswith("_") or path.name == "__init__.py":
            continue
        try:
            validate_plugin_source(path.read_text(encoding="utf-8"))
            validate_plugin_source(path.read_text(encoding="utf-8"))
            functions = _plugin_functions(path)
            metadata.append({
                "name": path.stem,
                "module": f"tools.plugins.{path.stem}",
                "functions": [name for name, _ in functions],
            })
        except Exception:
            continue
    return metadata


def create_plugin_tool(name: str, description: str, code: str, *, module_name: str | None = None):
    """Create a plugin module file, register it, and expose it as a callable tool."""
    safe_name = (name or "plugin_tool").strip().replace("-", "_")
    if not safe_name.isidentifier():
        raise ValueError("Plugin names must be valid Python identifiers")

    module_name = module_name or safe_name
    if not module_name.isidentifier():
        raise ValueError("Plugin module names must be valid Python identifiers")
    module_path = _PLUGIN_DIR / f"{module_name}.py"
    if module_path.resolve().parent != _PLUGIN_DIR.resolve():
        raise ValueError("Plugin module path must remain inside the plugin directory")

    body = code.strip()
    looks_like_module = (
        body.startswith("def ")
        or body.startswith("import ")
        or body.startswith("from ")
        or body.startswith("class ")
        or "\n" in body
    )
    if looks_like_module:
        module_content = '"""Auto-generated plugin tool."""\n\n' + body + "\n"
    else:
        if not body.startswith("return"):
            body = f"return {body}"
        function_source = f"def {safe_name}(**kwargs):\n    {body.replace(chr(10), chr(10) + '    ')}"
        module_content = (
            '"""Auto-generated plugin tool."""\n\n'
            f"{function_source}\n"
        )

    validate_plugin_source(module_content)

    module_path.write_text(module_content, encoding="utf-8")

    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    registry = []
    if _REGISTRY_PATH.exists():
        try:
            with open(_REGISTRY_PATH) as fh:
                registry = json.load(fh)
        except Exception:
            registry = []
    if not isinstance(registry, list):
        registry = []

    entry = {
        "name": safe_name,
        "module": module_name,
        "description": description,
        "path": str(module_path),
    }
    if not any(item.get("name") == safe_name for item in registry):
        registry.append(entry)
    with open(_REGISTRY_PATH, "w") as fh:
        json.dump(registry, fh, indent=2)

    importlib.invalidate_caches()
    return {"success": True, "name": safe_name, "module": module_name, "path": str(module_path), "tool": SandboxedPlugin(module_name, safe_name, description)}
