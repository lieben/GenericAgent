"""Life memory plugin package.

Reusable relationship/life memory storage for GA-style agents.
Agent data is isolated under data/agents/<agent>/life_memory/.

Gate v2 adds:
- noise-gated write path
- unified read path (life + journal + dream candidates) as side_context
- human-in-the-loop candidate promote helpers
"""
from .core import (
    LifeMemoryStore,
    MemoryEvent,
    remember_text,
    build_context,
    build_unified_context,
    get_store,
    list_candidates,
    promote_candidate,
    parse_dream_candidates,
    load_journal_recalls,
)

__all__ = [
    "LifeMemoryStore",
    "MemoryEvent",
    "remember_text",
    "build_context",
    "build_unified_context",
    "get_store",
    "list_candidates",
    "promote_candidate",
    "parse_dream_candidates",
    "load_journal_recalls",
]
