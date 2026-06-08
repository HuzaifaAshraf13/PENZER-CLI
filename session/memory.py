"""Agent memory module for persistent session memory."""

import json
import os
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

# Memory storage file
MEMORY_FILE = "memory_store/agent_memory.json"


def _ensure_memory_dir():
    """Ensure memory storage directory exists."""
    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)


def load_memory() -> Dict[str, Any]:
    """Load agent memory from persistent storage.
    
    Returns:
        Dictionary containing agent memory
    """
    _ensure_memory_dir()
    
    try:
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load memory: {e}")
    
    # Return empty memory structure if file doesn't exist or error occurred
    return {
        "observations": [],
        "findings": [],
        "targets": {},
        "credentials": {},
        "techniques": {},
        "metadata": {}
    }


def save_memory(memory: Dict[str, Any]) -> bool:
    """Save agent memory to persistent storage.
    
    Args:
        memory: Dictionary containing agent memory
        
    Returns:
        True if successful, False otherwise
    """
    _ensure_memory_dir()
    
    try:
        with open(MEMORY_FILE, 'w') as f:
            json.dump(memory, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save memory: {e}")
        return False


def update_memory(memory: Dict[str, Any], key: str, value: Any) -> None:
    """Update a specific memory key.
    
    Args:
        memory: Memory dictionary to update
        key: Key to update
        value: Value to set
    """
    memory[key] = value


def clear_memory() -> bool:
    """Clear all agent memory.
    
    Returns:
        True if successful, False otherwise
    """
    try:
        if os.path.exists(MEMORY_FILE):
            os.remove(MEMORY_FILE)
        return True
    except Exception as e:
        logger.error(f"Failed to clear memory: {e}")
        return False
