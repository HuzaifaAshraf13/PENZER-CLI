# Tracks task state, memory, progress
class Session:
    def __init__(self, task):
        self.task = task
        self.memory = []
        self.progress = 0
