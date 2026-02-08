"""
Audit logging utility for tracking all orchestrator actions.
Provides structured, searchable audit trail for compliance and debugging.
"""

import logging
import json
from typing import Dict, Any, Optional
from datetime import datetime
from enum import Enum


logger = logging.getLogger(__name__)


class AuditAction(Enum):
    """Types of auditable actions"""
    DEPLOYMENT_RECEIVED = "deployment_received"
    FAILURE_DETECTED = "failure_detected"
    RETRY_INITIATED = "retry_initiated"
    ROLLBACK_INITIATED = "rollback_initiated"
    ROLLBACK_COMPLETED = "rollback_completed"
    ROLLBACK_FAILED = "rollback_failed"
    HEALTH_CHECK_STARTED = "health_check_started"
    HEALTH_CHECK_PASSED = "health_check_passed"
    HEALTH_CHECK_FAILED = "health_check_failed"
    STATE_SAVED = "state_saved"
    STATE_RETRIEVED = "state_retrieved"
    ALERT_SENT = "alert_sent"
    MANUAL_INTERVENTION_REQUIRED = "manual_intervention_required"


class AuditLogger:
    """
    Structured audit logger for orchestrator actions.
    All audit events are logged in JSON format for easy parsing and searching.
    """
    
    def __init__(self, service_name: str = "self-healing-orchestrator"):
        self.service_name = service_name
        self.logger = logging.getLogger(f"{service_name}.audit")
        self.logger.setLevel(logging.INFO)
    
    def log_event(
        self,
        action: AuditAction,
        deployment_id: Optional[str] = None,
        namespace: Optional[str] = None,
        deployment_name: Optional[str] = None,
        version: Optional[str] = None,
        success: bool = True,
        details: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None
    ):
        """
        Log an audit event with full context.
        
        Args:
            action: Type of action being audited
            deployment_id: Unique deployment identifier
            namespace: Kubernetes namespace
            deployment_name: Name of the deployment
            version: Application version
            success: Whether the action succeeded
            details: Additional context details
            error: Error message if action failed
        """
        audit_event = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "service": self.service_name,
            "action": action.value,
            "success": success,
        }
        
        # Add deployment context if provided
        if deployment_id:
            audit_event["deployment_id"] = deployment_id
        if namespace:
            audit_event["namespace"] = namespace
        if deployment_name:
            audit_event["deployment_name"] = deployment_name
        if version:
            audit_event["version"] = version
        
        # Add details
        if details:
            audit_event["details"] = details
        
        # Add error if present
        if error:
            audit_event["error"] = error
        
        # Log as structured JSON
        self.logger.info(
            f"AUDIT: {action.value}",
            extra={"audit_event": json.dumps(audit_event)}
        )
    
    def log_deployment_received(
        self,
        deployment_id: str,
        namespace: str,
        deployment_name: str,
        version: str,
        status: str,
        failure_type: Optional[str] = None
    ):
        """Log when a deployment webhook is received"""
        self.log_event(
            action=AuditAction.DEPLOYMENT_RECEIVED,
            deployment_id=deployment_id,
            namespace=namespace,
            deployment_name=deployment_name,
            version=version,
            success=True,
            details={
                "status": status,
                "failure_type": failure_type
            }
        )
    
    def log_failure_detected(
        self,
        deployment_id: str,
        namespace: str,
        deployment_name: str,
        version: str,
        failure_type: str,
        retry_count: int
    ):
        """Log when a deployment failure is detected"""
        self.log_event(
            action=AuditAction.FAILURE_DETECTED,
            deployment_id=deployment_id,
            namespace=namespace,
            deployment_name=deployment_name,
            version=version,
            success=True,
            details={
                "failure_type": failure_type,
                "retry_count": retry_count
            }
        )
    
    def log_retry_initiated(
        self,
        deployment_id: str,
        namespace: str,
        deployment_name: str,
        version: str,
        retry_count: int,
        backoff_seconds: float
    ):
        """Log when a retry is initiated"""
        self.log_event(
            action=AuditAction.RETRY_INITIATED,
            deployment_id=deployment_id,
            namespace=namespace,
            deployment_name=deployment_name,
            version=version,
            success=True,
            details={
                "retry_count": retry_count,
                "backoff_seconds": backoff_seconds,
                "message": f"Retrying deployment (attempt {retry_count})"
            }
        )
    
    def log_rollback_initiated(
        self,
        deployment_id: str,
        namespace: str,
        deployment_name: str,
        current_version: str,
        target_version: str,
        reason: str
    ):
        """Log when a rollback is initiated"""
        self.log_event(
            action=AuditAction.ROLLBACK_INITIATED,
            deployment_id=deployment_id,
            namespace=namespace,
            deployment_name=deployment_name,
            version=current_version,
            success=True,
            details={
                "target_version": target_version,
                "reason": reason,
                "message": f"Rolling back from {current_version} to {target_version}"
            }
        )
    
    def log_rollback_completed(
        self,
        deployment_id: str,
        namespace: str,
        deployment_name: str,
        target_version: str,
        duration_seconds: float
    ):
        """Log when a rollback completes successfully"""
        self.log_event(
            action=AuditAction.ROLLBACK_COMPLETED,
            deployment_id=deployment_id,
            namespace=namespace,
            deployment_name=deployment_name,
            version=target_version,
            success=True,
            details={
                "duration_seconds": round(duration_seconds, 2),
                "message": f"Successfully rolled back to version {target_version}"
            }
        )
    
    def log_rollback_failed(
        self,
        deployment_id: str,
        namespace: str,
        deployment_name: str,
        target_version: str,
        error: str
    ):
        """Log when a rollback fails"""
        self.log_event(
            action=AuditAction.ROLLBACK_FAILED,
            deployment_id=deployment_id,
            namespace=namespace,
            deployment_name=deployment_name,
            version=target_version,
            success=False,
            error=error,
            details={
                "message": f"Rollback to {target_version} failed"
            }
        )
    
    def log_health_check_started(
        self,
        deployment_id: str,
        namespace: str,
        deployment_name: str,
        timeout_seconds: int
    ):
        """Log when a health check starts"""
        self.log_event(
            action=AuditAction.HEALTH_CHECK_STARTED,
            deployment_id=deployment_id,
            namespace=namespace,
            deployment_name=deployment_name,
            success=True,
            details={
                "timeout_seconds": timeout_seconds
            }
        )
    
    def log_health_check_passed(
        self,
        deployment_id: str,
        namespace: str,
        deployment_name: str,
        duration_seconds: float,
        ready_replicas: int,
        desired_replicas: int
    ):
        """Log when a health check passes"""
        self.log_event(
            action=AuditAction.HEALTH_CHECK_PASSED,
            deployment_id=deployment_id,
            namespace=namespace,
            deployment_name=deployment_name,
            success=True,
            details={
                "duration_seconds": round(duration_seconds, 2),
                "ready_replicas": ready_replicas,
                "desired_replicas": desired_replicas
            }
        )
    
    def log_health_check_failed(
        self,
        deployment_id: str,
        namespace: str,
        deployment_name: str,
        reason: str,
        ready_replicas: int,
        desired_replicas: int
    ):
        """Log when a health check fails"""
        self.log_event(
            action=AuditAction.HEALTH_CHECK_FAILED,
            deployment_id=deployment_id,
            namespace=namespace,
            deployment_name=deployment_name,
            success=False,
            error=reason,
            details={
                "ready_replicas": ready_replicas,
                "desired_replicas": desired_replicas
            }
        )
    
    def log_manual_intervention_required(
        self,
        deployment_id: str,
        namespace: str,
        deployment_name: str,
        version: str,
        reason: str
    ):
        """Log when manual intervention is required"""
        self.log_event(
            action=AuditAction.MANUAL_INTERVENTION_REQUIRED,
            deployment_id=deployment_id,
            namespace=namespace,
            deployment_name=deployment_name,
            version=version,
            success=False,
            details={
                "reason": reason,
                "message": "Automatic recovery exhausted, manual intervention required"
            }
        )


# Global audit logger instance
_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """Get the global audit logger instance"""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger