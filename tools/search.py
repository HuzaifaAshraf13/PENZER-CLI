# tools/search.py
import importlib
import pkgutil
import inspect
from typing import List, Dict, Any

def discover_tools_from_module(module_name: str):
    """
    Discover callables in a module that follow a naming convention (e.g. exported __all__ or functions prefixed with 'tool_').
    Returns list of metadata dicts: {"name": str, "callable": callable, "description": str}
    """
    module = importlib.import_module(module_name)
    found = []
    # prefer module.__all__ if present
    names = getattr(module, "__all__", None) or [n for n in dir(module) if not n.startswith("_")]
    for name in names:
        attr = getattr(module, name, None)
        if callable(attr):
            doc = inspect.getdoc(attr) or ""
            found.append({"name": name, "callable": attr, "description": doc})
    return found

def discover_tools(package_name: str = "tools"):
    """
    Discover tools within the 'tools' package: it will scan modules in the package and return discovered functions.
    """
    try:
        pkg = importlib.import_module(package_name)
    except Exception:
        return []

    discovered = []
    for _, modname, ispkg in pkgutil.iter_modules(pkg.__path__, package_name + "."):
        if ispkg:
            continue
        try:
            discovered.extend(discover_tools_from_module(modname))
        except Exception:
            continue
    return discovered
