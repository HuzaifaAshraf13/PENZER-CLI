# tools/tools.py
import google.generativeai as genai
from fastmcp import FastMCP
from typing import Callable, Dict, Any, List

mcp = FastMCP("Penzer MCP Server")  # MCP server instance

class ToolMeta:
    def __init__(self, func: Callable, name: str, description: str, parameters: dict):
        self.func = func
        self.name = name
        self.description = description
        self.parameters = parameters

class ToolManager:
    def __init__(self):
        self.tools: Dict[str, Callable] = {}
        self._metas: Dict[str, ToolMeta] = {}

    def register_tool(self, func: Callable, name: str = None, description: str = "", parameters: dict = None):
        """Register a local tool and decorate with @mcp.tool properly."""
        tool_name = name or func.__name__

        if tool_name in self.tools:
            raise ValueError(f"Tool {tool_name} already registered.")

        self.tools[tool_name] = func
        self._metas[tool_name] = ToolMeta(func, tool_name, description, parameters or {})

        # Proper decorator syntax usage
        decorator = mcp.tool(name=tool_name, description=description, inputSchema=parameters or {})
        decorator(func)  # apply decorator directly

    def load_example_tools(self):
        """Load a simple echo tool as an example"""
        def echo_tool(query: str):
            return {"echo": query}

        self.register_tool(
            echo_tool,
            name="echo",
            description="Echo back the provided string",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"]
            }
        )

    def get_tools_for_gemini(self) -> List:
        """Return Gemini-compatible tool definitions"""
        proto_defs = []
        for name, meta in self._metas.items():
            try:
                proto_defs.append(
                    genai.protos.Tool(
                        function_declarations=[
                            genai.protos.FunctionDeclaration(
                                name=name,
                                description=meta.description,
                                parameters=meta.parameters
                            )
                        ]
                    )
                )
            except Exception:
                proto_defs.append({
                    "name": name,
                    "description": meta.description,
                    "parameters": meta.parameters
                })
        return proto_defs
