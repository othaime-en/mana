import pytest
import json
import logging
from io import StringIO
from src.utils import (
    AuditLogger,
    AuditAction,
    get_audit_logger
)


@pytest.fixture
def audit_logger():
    """Create audit logger instance"""
    return AuditLogger(service_name="test-orchestrator")


class TestAuditLogger:
    """Test audit logging functionality"""
    
    def test_audit_logger_initialization(self, audit_logger):
        """Test audit logger initializes correctly"""
        assert audit_logger.service_name == "test-orchestrator"
        assert audit_logger.logger is not None
    
    def test_log_event_basic(self, audit_logger, caplog):
        """Test basic event logging"""
        with caplog.at_level(logging.INFO):
            audit_logger.log_event(
                action=AuditAction.DEPLOYMENT_RECEIVED,
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                version='1.0.0',
                success=True
            )
        
        assert 'AUDIT: deployment_received' in caplog.text
    
    def test_log_event_with_details(self, audit_logger, caplog):
        """Test event logging with additional details"""
        with caplog.at_level(logging.INFO):
            audit_logger.log_event(
                action=AuditAction.FAILURE_DETECTED,
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                version='1.0.0',
                success=True,
                details={
                    'failure_type': 'health_check_failure',
                    'retry_count': 1
                }
            )
        
        assert 'AUDIT: failure_detected' in caplog.text
    
    def test_log_event_with_error(self, audit_logger, caplog):
        """Test event logging with error"""
        with caplog.at_level(logging.INFO):
            audit_logger.log_event(
                action=AuditAction.ROLLBACK_FAILED,
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                version='1.0.0',
                success=False,
                error='Health check failed after rollback'
            )
        
        assert 'AUDIT: rollback_failed' in caplog.text
    
    def test_log_deployment_received(self, audit_logger, caplog):
        """Test deployment received logging"""
        with caplog.at_level(logging.INFO):
            audit_logger.log_deployment_received(
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                version='1.0.0',
                status='failed',
                failure_type='health_check_failure'
            )
        
        assert 'deployment_received' in caplog.text
    
    def test_log_failure_detected(self, audit_logger, caplog):
        """Test failure detection logging"""
        with caplog.at_level(logging.INFO):
            audit_logger.log_failure_detected(
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                version='1.0.0',
                failure_type='deployment_failure',
                retry_count=1
            )
        
        assert 'failure_detected' in caplog.text
    
    def test_log_retry_initiated(self, audit_logger, caplog):
        """Test retry initiation logging"""
        with caplog.at_level(logging.INFO):
            audit_logger.log_retry_initiated(
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                version='1.0.0',
                retry_count=2,
                backoff_seconds=20.0
            )
        
        assert 'retry_initiated' in caplog.text
    
    def test_log_rollback_initiated(self, audit_logger, caplog):
        """Test rollback initiation logging"""
        with caplog.at_level(logging.INFO):
            audit_logger.log_rollback_initiated(
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                current_version='1.0.0',
                target_version='0.9.0',
                reason='Exceeded retry threshold'
            )
        
        assert 'rollback_initiated' in caplog.text
    
    def test_log_rollback_completed(self, audit_logger, caplog):
        """Test rollback completion logging"""
        with caplog.at_level(logging.INFO):
            audit_logger.log_rollback_completed(
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                target_version='0.9.0',
                duration_seconds=45.5
            )
        
        assert 'rollback_completed' in caplog.text
    
    def test_log_rollback_failed(self, audit_logger, caplog):
        """Test rollback failure logging"""
        with caplog.at_level(logging.INFO):
            audit_logger.log_rollback_failed(
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                target_version='0.9.0',
                error='Health check failed'
            )
        
        assert 'rollback_failed' in caplog.text
    
    def test_log_health_check_started(self, audit_logger, caplog):
        """Test health check start logging"""
        with caplog.at_level(logging.INFO):
            audit_logger.log_health_check_started(
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                timeout_seconds=300
            )
        
        assert 'health_check_started' in caplog.text
    
    def test_log_health_check_passed(self, audit_logger, caplog):
        """Test health check success logging"""
        with caplog.at_level(logging.INFO):
            audit_logger.log_health_check_passed(
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                duration_seconds=15.5,
                ready_replicas=3,
                desired_replicas=3
            )
        
        assert 'health_check_passed' in caplog.text
    
    def test_log_health_check_failed(self, audit_logger, caplog):
        """Test health check failure logging"""
        with caplog.at_level(logging.INFO):
            audit_logger.log_health_check_failed(
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                reason='Timeout',
                ready_replicas=2,
                desired_replicas=3
            )
        
        assert 'health_check_failed' in caplog.text
    
    def test_log_manual_intervention_required(self, audit_logger, caplog):
        """Test manual intervention logging"""
        with caplog.at_level(logging.INFO):
            audit_logger.log_manual_intervention_required(
                deployment_id='test-123',
                namespace='production',
                deployment_name='sample-app',
                version='1.0.0',
                reason='Rollback failed'
            )
        
        assert 'manual_intervention_required' in caplog.text


class TestGetAuditLogger:
    """Test audit logger singleton"""
    
    def test_get_audit_logger_returns_instance(self):
        """Test get_audit_logger returns instance"""
        logger1 = get_audit_logger()
        logger2 = get_audit_logger()
        
        assert logger1 is not None
        assert logger1 is logger2  # Should be same instance
    
    def test_audit_logger_is_singleton(self):
        """Test audit logger is a singleton"""
        logger1 = get_audit_logger()
        logger2 = get_audit_logger()
        
        assert id(logger1) == id(logger2)


class TestAuditAction:
    """Test audit action enum"""
    
    def test_audit_action_values(self):
        """Test audit action enum values"""
        assert AuditAction.DEPLOYMENT_RECEIVED.value == "deployment_received"
        assert AuditAction.FAILURE_DETECTED.value == "failure_detected"
        assert AuditAction.RETRY_INITIATED.value == "retry_initiated"
        assert AuditAction.ROLLBACK_INITIATED.value == "rollback_initiated"
        assert AuditAction.ROLLBACK_COMPLETED.value == "rollback_completed"
        assert AuditAction.ROLLBACK_FAILED.value == "rollback_failed"
        assert AuditAction.HEALTH_CHECK_STARTED.value == "health_check_started"
        assert AuditAction.HEALTH_CHECK_PASSED.value == "health_check_passed"
        assert AuditAction.HEALTH_CHECK_FAILED.value == "health_check_failed"
        assert AuditAction.MANUAL_INTERVENTION_REQUIRED.value == "manual_intervention_required"