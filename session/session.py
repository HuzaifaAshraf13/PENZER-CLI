# Tracks task state, memory, progress
class Session:
    def __init__(self, initial_task=None):
        self.messages = []
        if initial_task:
            self.messages.append({"role": "system", "content": f"Initial task: {initial_task}"})
        self.progress = 0

    def add_message(self, role, content):
        self.messages.append({"role": role, "content": content})
        print(f"Session: {role.capitalize()}: {content}")
