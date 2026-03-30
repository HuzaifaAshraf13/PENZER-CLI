# agent/core.py
"""
Core MCP and Memory initialization for Penzer agent.
Handles FastMCP server setup and ReMeApp long-term memory integration.
"""

import os
import asyncio
import logging

# Configure logging
logger = logging.getLogger("penzer.core")
logger.setLevel(logging.INFO)

# Disable verbose logging from dependencies BEFORE importing them
try:
    from loguru import logger as loguru_logger
    loguru_logger.disable("flowllm")
    loguru_logger.disable("reme_ai")
except:
    pass

# Suppress dependency logging
os.environ['FLOWLLM_LOG_LEVEL'] = 'ERROR'

import logging as stdlib_logging
stdlib_logging.basicConfig(level=stdlib_logging.WARNING)
for logger_name in ["flowllm", "reme_ai", "base_flow", "timer"]:
    stdlib_logging.getLogger(logger_name).setLevel(stdlib_logging.ERROR)

from fastmcp import FastMCP
from reme_ai import ReMeApp

# ================== SINGLE MCP INSTANCE ==================
mcp = FastMCP(name="PenzerMCP")
logger.info(f"✓ FastMCP instance created: {mcp.name}")

# ================== LONG-TERM MEMORY (ReMeApp) ==================
db_path = os.path.join(os.getcwd(), "memory_store")
os.makedirs(db_path, exist_ok=True)

# Initialize ReMeApp with proper error handling
reme_app = None
try:
    reme_app = ReMeApp()
    logger.info("✓ ReMeApp object created successfully")
except Exception as e:
    logger.warning(f"ReMeApp instantiation warning: {e}")
    reme_app = None

# Track initialization state
_reme_initialized = False

async def init_reme():
    """
    Initialize ReMeApp context manager for long-term memory.
    Must be called before using mem_get_long/mem_set_long tools.
    
    Returns:
        bool: True if initialization successful, False otherwise
    """
    global _reme_initialized
    
    if reme_app is None:
        logger.warning("ReMeApp not available - using short-term memory only")
        _reme_initialized = False
        return False
    
    try:
        await reme_app.__aenter__()
        _reme_initialized = True
        logger.info("✓ Long-term memory (ReMeApp) initialized successfully")
        return True
    except Exception as e:
        logger.error(f"ReMeApp context initialization failed: {e}")
        logger.warning("Falling back to short-term memory only")
        _reme_initialized = False
        return False

def is_reme_initialized():
    """Check if ReMeApp has been initialized and is ready."""
    return _reme_initialized

async def cleanup_reme():
    """
    Clean up ReMeApp context manager.
    Should be called on agent shutdown.
    """
    global _reme_initialized
    
    if not _reme_initialized or reme_app is None:
        return
    
    try:
        await reme_app.__aexit__(None, None, None)
        _reme_initialized = False
        logger.info("✓ ReMeApp cleaned up successfully")
    except Exception as e:
        logger.warning(f"ReMeApp cleanup error: {e}")
        _reme_initialized = False


# ================== MCP UTILITIES ==================

async def get_mcp_tools():
    """Get list of all registered MCP tools."""
    try:
        if hasattr(mcp, "get_tools") and callable(getattr(mcp, "get_tools")):
            tools_dict = await mcp.get_tools()
            return list(tools_dict.keys())
        elif hasattr(mcp, "tools"):
            return list(mcp.tools.keys())
        elif hasattr(mcp, "_tools"):
            return list(mcp._tools.keys())
    except Exception as e:
        logger.debug(f"Error fetching MCP tools: {e}")
    return []

async def get_mcp_resources():
    """Get list of all registered MCP resources."""
    try:
        resources = getattr(mcp, "resources", {})
        return list(resources.keys())
    except Exception as e:
        logger.debug(f"Error fetching MCP resources: {e}")
    return []

def get_mcp_status():
    """Get current MCP server status."""
    return {
        "name": mcp.name,
        "status": "ready"
    }
