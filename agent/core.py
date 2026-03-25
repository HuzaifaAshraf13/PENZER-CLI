# agent/core.py
from fastmcp import FastMCP
from reme_ai import ReMeApp
import os
import asyncio

# ---------------- SINGLE MCP INSTANCE ----------------
mcp = FastMCP(name="PenzerMCP")

# ---------------- REFERENCE MEMORY INSTANCE ----------------
db_path = os.path.join(os.getcwd(), "memory_store")
os.makedirs(db_path, exist_ok=True)

# Initialize ReMeApp with proper error handling
reme_app = None
try:
    # Try to initialize ReMeApp with default settings
    reme_app = ReMeApp()
    print("[CORE] ReMeApp object created")
except Exception as e:
    print(f"[CORE] ReMeApp instantiation warning: {e}")
    reme_app = None

# Track initialization state
_reme_initialized = False

async def init_reme():
    """Initialize ReMeApp context manager. Must be called before using long-term memory."""
    global _reme_initialized
    
    if reme_app is None:
        print("[CORE] ReMeApp not available - using short-term memory only")
        return False
    
    try:
        await reme_app.__aenter__()
        _reme_initialized = True
        print("[CORE] ✓ Long-term memory (ReMeApp) initialized successfully")
        return True
    except Exception as e:
        print(f"[CORE] ReMeApp context initialization failed: {e}")
        print("[CORE] Using short-term memory only")
        _reme_initialized = False
        return False

def is_reme_initialized():
    """Check if ReMeApp has been initialized."""
    return _reme_initialized

async def cleanup_reme():
    """Clean up ReMeApp context manager."""
    global _reme_initialized
    try:
        if _reme_initialized and reme_app is not None:
            await reme_app.__aexit__(None, None, None)
            _reme_initialized = False
            print("[CORE] ReMeApp cleaned up")
    except Exception as e:
        print(f"[CORE] ReMeApp cleanup warning: {e}")
        _reme_initialized = False

        print(f"[CORE] ReMeApp cleanup warning: {e}")
