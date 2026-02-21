
from core.cerebrum import Skill
from core.memory import MemorySystem

class MemorySkill(Skill):
    name = "Memory"
    description = "Access the Hippocampus to store and retrieve long-term memories."
    commands = ["remember", "recall"]

    def __init__(self):
        # We can reuse the MemorySystem instance if passed, 
        # or instantiate a new one (it connects to the same DB).
        # For simplicity, we instantiate.
        self.memory = MemorySystem()

    def execute(self, command: str, *args, **kwargs) -> str:
        if command == "remember":
            if not args:
                return "Usage: remember <text>"
            
            text = " ".join(args)
            # We could extract metadata from args if complex parsing, 
            # but for now just store the text.
            self.memory.store_memory(text)
            return f"Memory stored in Hippocampus: '{text}'"

        elif command == "recall":
            if not args:
                return "Usage: recall <query>"
            
            query = " ".join(args)
            results = self.memory.retrieve_relevant_memories(query, n_results=3)
            
            if not results:
                return "No relevant memories found."
            
            response = "Recall Results:
"
            for i, mem in enumerate(results):
                response += f"{i+1}. {mem}
"
            return response.strip()

        return f"Unknown command: {command}"
