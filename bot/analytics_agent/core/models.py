from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict
import time
import uuid


@dataclass
class ResearchTask:
    task_id: str
    profile: str
    user_text: str
    params: Dict[str, Any] = field(default_factory=dict)
    status: str = "draft"
    created_at: float = field(default_factory=time.time)

    @classmethod
    def create(cls, profile: str, user_text: str, params: Dict[str, Any] | None = None) -> "ResearchTask":
        return cls(
            task_id=uuid.uuid4().hex[:12],
            profile=profile,
            user_text=user_text.strip(),
            params=params or {},
        )