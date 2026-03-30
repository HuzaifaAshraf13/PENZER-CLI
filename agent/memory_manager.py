"""
Memory Manager for Penzer Agent
Handles short-term and long-term memory with search and consolidation
"""

import logging
import json
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    Manages both short-term and long-term memory.
    Provides search, storage, and consolidation capabilities.
    """
    
    def __init__(self, tool_executor):
        """Initialize memory manager"""
        self.tool_executor = tool_executor
        self.short_term: Dict[str, Any] = {}
        self.long_term: Dict[str, Any] = {}
        logger.info("MemoryManager initialized")
    
    async def load_memory(self, workspace_id: str) -> None:
        """Load memory from persistent storage"""
        try:
            # Load short-term memory
            result = await self.tool_executor.run_tool(
                "mem_get_short",
                {"workspace_id": workspace_id}
            )
            
            if result.get("status") == "success":
                self.short_term = result.get("data", {})
                logger.info(f"Loaded short-term memory: {len(self.short_term)} entries")
            
            # Load long-term memory
            result = await self.tool_executor.run_tool(
                "mem_get_long",
                {"workspace_id": workspace_id}
            )
            
            if result.get("status") == "success":
                self.long_term = result.get("data", {})
                logger.info(f"Loaded long-term memory: {len(self.long_term)} entries")
        
        except Exception as e:
            logger.error(f"Failed to load memory: {e}")
    
    async def save_short_term(
        self,
        workspace_id: str,
        key: str,
        value: Any
    ) -> bool:
        """Save entry to short-term memory"""
        try:
            self.short_term[key] = value
            
            result = await self.tool_executor.run_tool(
                "mem_set_short",
                {
                    "workspace_id": workspace_id,
                    key: value
                }
            )
            
            success = result.get("status") == "success"
            if success:
                logger.debug(f"Saved to short-term memory: {key}")
            return success
        
        except Exception as e:
            logger.error(f"Failed to save short-term memory: {e}")
            return False
    
    async def save_long_term(
        self,
        workspace_id: str,
        key: str,
        value: Any
    ) -> bool:
        """Save entry to long-term memory with timestamp"""
        try:
            # Add timestamp and metadata
            value_with_metadata = {
                "content": value,
                "timestamp": datetime.now().isoformat(),
                "source": "agent_learning"
            }
            
            self.long_term[key] = value_with_metadata
            
            result = await self.tool_executor.run_tool(
                "mem_set_long",
                {
                    "workspace_id": workspace_id,
                    key: value_with_metadata
                }
            )
            
            success = result.get("status") == "success"
            if success:
                logger.info(f"Saved to long-term memory: {key}")
            return success
        
        except Exception as e:
            logger.error(f"Failed to save long-term memory: {e}")
            return False
    
    async def search_memory(
        self,
        workspace_id: str,
        query: str,
        search_long_term: bool = True
    ) -> Dict[str, Any]:
        """
        Search memory for relevant information.
        Searches both short and long-term memory.
        """
        try:
            results = {
                "short_term": {},
                "long_term": {}
            }
            
            # Search short-term
            for key, value in self.short_term.items():
                if query.lower() in str(key).lower() or query.lower() in str(value).lower():
                    results["short_term"][key] = value
            
            # Search long-term if requested
            if search_long_term:
                for key, value in self.long_term.items():
                    if query.lower() in str(key).lower() or query.lower() in str(value).lower():
                        results["long_term"][key] = value
            
            logger.info(f"Memory search found {len(results['short_term']) + len(results['long_term'])} results")
            return results
        
        except Exception as e:
            logger.error(f"Memory search failed: {e}")
            return {"short_term": {}, "long_term": {}}
    
    async def consolidate_learning(
        self,
        workspace_id: str,
        session_summary: Dict[str, Any]
    ) -> bool:
        """
        Consolidate learnings from session into long-term memory.
        Extract key findings and insights for future reference.
        """
        try:
            logger.info("Consolidating session learning into long-term memory")
            
            # Extract key learnings
            learnings = {
                "session_date": datetime.now().isoformat(),
                "actions_taken": session_summary.get("actions_taken", 0),
                "tools_used": session_summary.get("tools_used", []),
                "findings": session_summary.get("findings", []),
                "success_rate": session_summary.get("success_rate", 0),
                "lessons_learned": session_summary.get("lessons_learned", [])
            }
            
            # Save consolidated learning
            key = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            await self.save_long_term(workspace_id, key, learnings)
            
            return True
        
        except Exception as e:
            logger.error(f"Failed to consolidate learning: {e}")
            return False
    
    def get_short_term_context(self, max_items: int = 10) -> Dict[str, Any]:
        """Get short-term memory context for LLM"""
        items = list(self.short_term.items())[-max_items:]
        return dict(items)
    
    def get_long_term_context(self, max_items: int = 5) -> Dict[str, Any]:
        """Get long-term memory context for LLM"""
        items = list(self.long_term.items())[-max_items:]
        return dict(items)
    
    def clear_short_term(self) -> None:
        """Clear short-term memory"""
        self.short_term.clear()
        logger.info("Short-term memory cleared")
    
    def summary(self) -> Dict[str, int]:
        """Get memory usage summary"""
        return {
            "short_term_entries": len(self.short_term),
            "long_term_entries": len(self.long_term),
            "total_memory": len(self.short_term) + len(self.long_term)
        }
