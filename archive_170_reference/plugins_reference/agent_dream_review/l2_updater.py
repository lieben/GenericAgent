"""Fail-closed compatibility shim for the retired dream-to-L2 writer.

The historical ``memory/agents/<agent>/L2_facts.txt`` file remains intact.
This compatibility module deliberately performs no file reads or writes.
"""

from typing import Any, Dict


def apply_l2_updates(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Reject legacy structured writes so stale callers cannot dual-write."""
    raise RuntimeError(
        "legacy dream-to-L2 writer retired; use the current GA native memory policy"
    )


def update_l2_facts(agent_name: str, new_facts_markdown: str) -> bool:
    """Reject the former boolean compatibility entrypoint."""
    raise RuntimeError(
        "legacy dream-to-L2 writer retired; use the current GA native memory policy"
    )
