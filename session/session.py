# session/session.py
from reme_ai import ReMe  # Change 'reme' to 'reme_ai'

class SessionManager:
    def __init__(self, user_id: str):
        self.user_id = user_id
        # Initialize ReMe with a persistent vector store
        self.memory = ReMe(storage_path=f"./data/memory/{user_id}")

    async def search(self, query: str):
        return await self.memory.recall(query)

    async def store(self, content: str, metadata: dict = None):
        return await self.memory.memorize(content, metadata=metadata)

# Global instance placeholder to be initialized by cli.py or agent.py
active_session: SessionManager = None