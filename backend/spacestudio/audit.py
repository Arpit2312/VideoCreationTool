"""Audit log: audit(event, **details) -> AuditLog row (details are redacted)."""
from __future__ import annotations

import logging
from typing import Any, Optional

from .models import AuditLog
from .security import redact_obj

log = logging.getLogger("spacestudio.audit")


def audit(event: str, actor: str = "system", video_id: Optional[str] = None, **details: Any) -> Optional[AuditLog]:
    from .db import session_scope

    row = AuditLog(event=event, actor=actor, video_id=video_id, details=redact_obj(details))
    try:
        with session_scope() as s:
            s.add(row)
            s.flush()
            s.refresh(row)
        return row
    except Exception:  # auditing must never break the caller
        log.exception("could not write audit event %s", event)
        return None
