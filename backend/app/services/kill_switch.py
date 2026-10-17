"""Kill switch service — durable cross-process emergency shutdown."""
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from app.models.kill_switch import KillSwitch, KillSwitchScope
from app.lib.timeutil import utcnow


def is_kill_switch_active(db: Session, scope: KillSwitchScope) -> bool:
    """Check if kill switch is active for the given scope.
    
    Returns True if:
    - Global kill switch is active, OR
    - Scope-specific kill switch is active
    
    This is a read-only check. If the database is unreachable, this will raise
    an exception. Callers should decide whether to fail-open or fail-closed.
    
    For production safety, we fail-CLOSED: if we can't read the switch state,
    we assume it's active and block the operation.
    """
    # Check global switch first
    global_switch = db.query(KillSwitch).filter(
        KillSwitch.scope == KillSwitchScope.global_,
        KillSwitch.is_active == True,
    ).first()
    
    if global_switch:
        return True
    
    # Check scope-specific switch
    scope_switch = db.query(KillSwitch).filter(
        KillSwitch.scope == scope,
        KillSwitch.is_active == True,
    ).first()
    
    return scope_switch is not None


def activate_kill_switch(
    db: Session,
    scope: KillSwitchScope,
    activated_by: str,
    reason: Optional[str] = None,
) -> KillSwitch:
    """Activate kill switch for the given scope.
    
    Creates or updates the kill switch record.
    Returns the updated kill switch.
    """
    switch = db.query(KillSwitch).filter(KillSwitch.scope == scope).first()
    
    if not switch:
        switch = KillSwitch(scope=scope)
        db.add(switch)
    
    switch.is_active = True
    switch.activated_at = utcnow()
    switch.activated_by = activated_by
    switch.reason = reason
    switch.updated_at = utcnow()
    
    db.commit()
    db.refresh(switch)
    return switch


def deactivate_kill_switch(db: Session, scope: KillSwitchScope) -> Optional[KillSwitch]:
    """Deactivate kill switch for the given scope.
    
    Returns the updated kill switch, or None if no switch existed.
    """
    switch = db.query(KillSwitch).filter(KillSwitch.scope == scope).first()
    
    if not switch:
        return None
    
    switch.is_active = False
    switch.updated_at = utcnow()
    
    db.commit()
    db.refresh(switch)
    return switch


def get_kill_switch(db: Session, scope: KillSwitchScope) -> Optional[KillSwitch]:
    """Get kill switch record for the given scope."""
    return db.query(KillSwitch).filter(KillSwitch.scope == scope).first()


def list_kill_switches(db: Session) -> list[KillSwitch]:
    """List all kill switches."""
    return db.query(KillSwitch).order_by(KillSwitch.scope).all()
