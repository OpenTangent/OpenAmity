"""
core/memory_records.py — Structured memory records and retrieval contracts for Open Amity.
Implements M1 retrieval contract:
- MemoryHit: logical drawer ID, matched physical chunk IDs, preview, truncation flag, score/metric, metadata.
- MemoryRecord: logical drawer ID, ordered hydrated content, metadata, total length, revision, chunks count.
- MemoryResult: typed result container distinguishing success, no results, missing ID, and store failure.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass
class MemoryHit:
    logical_id: str
    chunk_ids: List[str] = field(default_factory=list)
    preview: str = ""
    is_truncated: bool = False
    score: Optional[float] = None
    metric: str = "cosine"
    wing: str = "default"
    room: str = "general"
    source_file: str = ""
    authored_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "logical_id": self.logical_id,
            "chunk_ids": list(self.chunk_ids),
            "preview": self.preview,
            "is_truncated": self.is_truncated,
            "score": self.score,
            "metric": self.metric,
            "wing": self.wing,
            "room": self.room,
            "source_file": self.source_file,
            "authored_at": self.authored_at,
            "metadata": dict(self.metadata),
        }


@dataclass
class MemoryRecord:
    drawer_id: str
    content: str = ""
    wing: str = "default"
    room: str = "general"
    metadata: Dict[str, Any] = field(default_factory=dict)
    total_chars: int = 0
    chunks: int = 1
    chunk_ids: List[str] = field(default_factory=list)
    revision: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "drawer_id": self.drawer_id,
            "content": self.content,
            "wing": self.wing,
            "room": self.room,
            "metadata": dict(self.metadata),
            "total_chars": self.total_chars,
            "chunks": self.chunks,
            "chunk_ids": list(self.chunk_ids),
            "revision": self.revision,
        }


@dataclass
class MemoryResult:
    success: bool
    status: str = "ok"  # "ok", "no_results", "missing_id", "store_failure", "error"
    hits: List[MemoryHit] = field(default_factory=list)
    record: Optional[MemoryRecord] = None
    payload: Any = None
    offset: int = 0
    limit: int = 0
    total_count: Optional[int] = 0
    has_more: bool = False
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status,
            "hits": [h.to_dict() for h in self.hits],
            "record": self.record.to_dict() if self.record else None,
            "payload": self.payload,
            "offset": self.offset,
            "limit": self.limit,
            "total_count": self.total_count,
            "has_more": self.has_more,
            "error": self.error,
            "warnings": list(self.warnings),
        }
