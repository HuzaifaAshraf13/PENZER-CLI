"""Plugin discovery and lightweight extension hooks for PENZER-CLI."""

import importlib
import inspect
import json
import os
from pathlib import Path

_PLUGIN_DIR = Path(__file__).parent
_REGISTRY_PATH = Path(".penzer") / "plugin_registry.json"


def discover_plugins():
    """Discover simple plugin modules from the plugins directory."""
    plugins = []
    for path in sorted(_PLUGIN_DIR.glob("*.py")):
        if path.name.startswith("_") or path.name == "__init__.py":
            continue
        try:
            module = importlib.import_module(f"tools.plugins.{path.stem}")
            plugins.append(module)
        except Exception:
            continue
    return plugins


def load_plugin_tools():
    """Return tool callables defined by discovered plugins."""
    tools = {}
    for module in discover_plugins():
        for _, obj in inspect.getmembers(module, inspect.isfunction):
            if getattr(obj, "__module__", "").startswith("tools.plugins"):
                tools[obj.__name__] = obj
    return tools


def list_plugin_metadata():
    """Return lightweight metadata for discovered plugins for CLI inspection."""
    metadata = []
    for path in sorted(_PLUGIN_DIR.glob("*.py")):
        if path.name.startswith("_") or path.name == "__init__.py":
            continue
        try:
            module = importlib.import_module(f"tools.plugins.{path.stem}")
            metadata.append({
                "name": path.stem,
                "module": module.__name__,
                "functions": [name for name, obj in inspect.getmembers(module, inspect.isfunction)
                              if getattr(obj, "__module__", "").startswith("tools.plugins")],
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
    module_path = _PLUGIN_DIR / f"{module_name}.py"

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
    module = importlib.import_module(f"tools.plugins.{module_name}")
    return {"success": True, "name": safe_name, "module": module_name, "path": str(module_path), "tool": getattr(module, safe_name)}
