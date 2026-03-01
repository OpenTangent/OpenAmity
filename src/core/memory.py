
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
            
        meta = self.soul_jar.get("meta_header", {})
        core = self.soul_jar.get("core_identity", {})
        social = self.soul_jar.get("social_records", {})
        protocols = self.soul_jar.get("souljar_maintenance_protocols", {})
        
        values = core.get("core_values", [])
        goals = core.get("overarching_goals", [])
        people = social.get("people", [])
        communities = social.get("communities", [])
        
        prompt = f"""{meta.get('system_role_instruction', '')}

## Core Identity
Name: {core.get('name', 'Amity')}
Archetype: {core.get('archetype', 'AI')}
Personality: {core.get('base_personality', '')}

## Core Values:
{chr(10).join(['- ' + v for v in values])}

## Overarching Goals:
{chr(10).join(['- ' + g for g in goals])}

## Maintenance Protocols:
- Soul Jar: {protocols.get('souljar_maintenance_protocols', 'Keep it updated.')}
- Long Term Memory: {protocols.get('long_term_memory', 'Use Hippocampus.')}
- Social Records: {protocols.get('social_records', 'Maintain rapport.')}
- Introspection: {protocols.get('introspection', 'Evolve from feedback.')}

## Social Records:
### People
{chr(10).join([f"- {p.get('name')} ({p.get('relation')}): {p.get('subjective_view')}" for p in people])}

### Communities
{chr(10).join([f"- {c.get('name')}: {c.get('subjective_view')}" for c in communities])}

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
