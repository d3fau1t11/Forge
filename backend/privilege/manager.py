import logging
from typing import Dict, Any
from sqlalchemy.orm import Session
from backend.database.models import AuditLogModel

logger = logging.getLogger("forge.privilege")

class PrivilegeManager:
    """Evaluates privilege requirements and maintains tamper-evident audit logs."""

    def evaluate_privilege(
        self,
        agent: str,
        tool_name: str,
        privilege_level: str,
        db: Session
    ) -> bool:
        """Determines if execution is permitted under security policy."""
        approved = False
        
        if privilege_level == "SAFE":
            approved = True
        elif privilege_level in ["PRIVILEGED", "DANGEROUS"]:
            # Requires operator session approval (defaults to pending approval in automated flow)
            approved = False

        # Log audit trail
        audit_entry = AuditLogModel(
            agent=agent,
            action=f"execute_tool:{tool_name}",
            privilege_level=privilege_level,
            approved=approved,
            details={"tool": tool_name}
        )
        db.add(audit_entry)
        db.commit()

        logger.info(f"Privilege check for agent '{agent}' requesting tool '{tool_name}' ({privilege_level}): approved={approved}")
        return approved

privilege_manager = PrivilegeManager()
