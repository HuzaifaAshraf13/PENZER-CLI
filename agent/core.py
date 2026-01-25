# agent/core.py
from fastmcp import FastMCP
from reme_ai import ReMeApp
import os

# ---------------- SINGLE MCP INSTANCE ----------------
mcp = FastMCP(name="PenzerMCP")

# ---------------- REFERENCE MEMORY INSTANCE ----------------
db_path = os.path.join(os.getcwd(), "memory_store")
os.makedirs(db_path, exist_ok=True)

reme_app = ReMeApp(
    "llm.default.model_name=qwen3-8b",
    "embedding_model.default.model_name=text-embedding-v4",
    "vector_store.default.backend=chroma",
    f"vector_store.chroma.path={db_path}"
)
