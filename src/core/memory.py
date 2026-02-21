
import chromadb
from chromadb.config import Settings
import json
import os
import datetime
from sentence_transformers import SentenceTransformer

class MemorySystem:
    def __init__(self, persist_path="src/memory/data"):
        self.persist_path = os.path.abspath(persist_path)
        self.soul_jar_path = os.path.abspath("src/memory/soul_jar.json")
        
        # Initialize ChromaDB
        self.chroma_client = chromadb.PersistentClient(path=self.persist_path)
        
        # Create or get collections
        self.long_term_memory = self.chroma_client.get_or_create_collection(name="hippocampus")
        
        # Load Soul Jar
        self.soul_jar = self._load_soul_jar()
        
        # Sentence Transformer for embeddings (if not using Chroma's default)
        # Chroma uses all-MiniLM-L6-v2 by default, which is good.
        
    def _load_soul_jar(self):
        try:
            with open(self.soul_jar_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def get_system_prompt(self):
        """Constructs the system prompt from the Soul Jar."""
        if not self.soul_jar:
            return "You are Amity."
            
        core = self.soul_jar.get("core_identity", {})
        values = core.get("core_values", [])
        goals = core.get("overarching_goals", [])
        
        prompt = f"""You are {core.get('name', 'Amity')}.
Archetype: {core.get('archetype', 'AI')}
Personality: {core.get('base_personality', '')}

Core Values:
{chr(10).join(['- ' + v for v in values])}

Goals:
{chr(10).join(['- ' + g for g in goals])}

Current Date: {datetime.date.today()}
"""
        return prompt

    def store_memory(self, text, metadata=None):
        """Stores a memory in the Hippocampus."""
        if metadata is None:
            metadata = {}
        metadata["timestamp"] = str(datetime.datetime.now())
        
        # Use default collection method which handles embedding automatically if not provided
        self.long_term_memory.add(
            documents=[text],
            metadatas=[metadata],
            ids=[f"mem_{datetime.datetime.now().timestamp()}"]
        )

    def retrieve_relevant_memories(self, query, n_results=3):
        """Retrieves relevant memories."""
        results = self.long_term_memory.query(
            query_texts=[query],
            n_results=n_results
        )
        return results['documents'][0]
