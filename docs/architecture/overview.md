# Architecture Overview

MANA (Self-Healing CI/CD Pipeline) is designed as a Kubernetes-native orchestration system that automatically detects and recovers from deployment failures. This document provides an overview of MANA's architecture, components, and data flow.

## High-Level Architecture

MANA consists of several loosely coupled components that work together to provide self-healing capabilities:

```
┌─────────────────┐    ┌──────────────────────┐    ┌────────────────────┐
│  CI/CD System   │───▶│   MANA Orchestrator   │───▶│ Kubernetes Cluster │
│ (GitHub Actions)│    │                      │    │                    │
└─────────────────┘    │  • API Server         │    │  • Deployments     │
                       │  • Decision Engine    │    │  • ReplicaSets     │
                       │  • State Manager      │    │  • Pods            │
                       │  • Audit Logger       │    │                    │
                       │  • Health Checker     │    └────────────────────┘
                       └──────────────────────┘
                                │
                                ▼
                      ┌────────────────────┐
                      │  Monitoring Stack  │
                      │ (Prometheus, Grafana)│
                      └────────────────────┘
                                │
                                ▼
                      ┌────────────────────┐
                      │  External Systems  │
                      │ (Slack, Email, etc.)│
                      └────────────────────┘
```

## Core Components

### 1. Orchestrator Service

The central decision-making component built with FastAPI that exposes a REST API for:
- Receiving deployment webhooks from CI/CD systems
- Managing deployment states
- Making retry/rollback decisions
- Providing health checks and metrics
- Exposing audit logs

**Key Responsibilities:**
- Process incoming deployment events
- Determine appropriate response (retry, rollback, alert)
- Maintain deployment state in Redis
- Execute rollback operations via Kubernetes API
- Integrate with GitHub Actions for workflow re-runs
- Expose Prometheus metrics for monitoring
- Generate structured audit logs

### 2. State Management (Redis)

Redis is used as the primary state store for:
- Tracking deployment states and history
- Storing successful deployment versions for rollback
- Maintaining retry counts and backoff timers
- Persisting audit trail information

**Why Redis?**
- High performance for frequent read/write operations
- Built-in expiration (TTL) for automatic cleanup
- Atomic operations for state consistency
- Rich data structures (hashes, lists, sets)
- Wide adoption and operational maturity

### 3. Kubernetes Integration

MANA interacts with Kubernetes through the official client-go library to:
- Monitor deployment and pod status
- Execute rollback operations by updating image tags
- Read ReplicaSet information for version tracking
- Create events for better observability
- Monitor namespaces for new deployments (optional)

**Key Kubernetes Resources Managed:**
- Deployments (primary target for monitoring and rollback)
- ReplicaSets (for version history and rollback targets)
- Pods (for health checking and readiness determination)
- Events (for operational visibility)

### 4. Health Checker

Responsible for determining the health of deployments through:
- Pod readiness checks (via Kubernetes API)
- Application-level health checks (via HTTP endpoints)
- Configurable failure thresholds
- Distinction between pod-level and application-level health

### 5. Decision Engine

Implements the core logic for determining when to retry vs. rollback:
- Failure detection and classification
- Retry count tracking
- Exponential backoff calculation
- Rollback threshold evaluation
- GitHub Actions integration for workflow re-runs

### 6. Audit Logger

Provides a comprehensive, immutable record of all actions:
- Structured JSON logging for easy parsing
- Context preservation (deployment ID, namespace, version, etc.)
- Success/failure tracking for all operations
- Detailed error information when applicable
- Configurable retention policies

### 7. Monitoring & Observability

Built-in instrumentation for operational visibility:
- Prometheus metrics for key operational metrics
- Structured logging for debugging and auditing
- Health checks for both service and dependency verification
- Distributed tracing readiness (through request IDs)
- Custom dashboards for operational visibility

## Data Flow

### Normal Deployment Flow

1. **Deployment Trigger**: CI/CD system deploys new version to Kubernetes
2. **Completion Notification**: CI/CD sends webhook to MANA with `status: success`
3. **State Update**: MANA records the version as successful in Redis
4. **Monitoring**: MANA continues to monitor deployment health
5. **Completion**: No further action needed if deployment remains healthy

### Failure Detection and Recovery Flow

1. **Failure Occurrence**: Deployment fails (health check fails, pods crash, etc.)
2. **Notification**: CI/CD sends webhook to MANA with `status: failed` and `failure_type`
3. **Failure Logging**: MANA logs the failure detection in audit log
4. **State Retrieval**: MANA retrieves current deployment state from Redis
5. **Decision Making**:
   - If first failure: Schedule retry with exponential backoff
   - If within retry limit: Trigger GitHub Actions workflow re-run
   - If retry threshold exceeded: Initiate rollback to previous stable version
6. **Recovery Execution**:
   - For retry: Wait for backoff period, then trigger CI/CD re-run
   - For rollback: Update deployment image tag to previous version
7. **Verification**: MANA monitors the recovery action to confirm success
8. **Completion**: Log recovery action in audit trail and return to monitoring

### Detailed Failure Handling Process

#### Retry Path
```
Failure Detected
       ↓
Get Deployment State
       ↓
Increment Retry Count
       ↓
Calculate Backoff Delay
       ↓
Save Updated State
       ↓
Schedule Retry (async)
       ↓
Trigger GitHub Workflow Re-run (after backoff)
       ↓
Monitor for Success/Failure
       ↓
If Success: Record as Successful Version
       ↓
If Failure: Repeat or Rollback (if threshold exceeded)
```

#### Rollback Path
```
Failure Detected
       ↓
Get Deployment State
       ↓
Check Retry Count vs Threshold
       ↓
If Threshold Exceeded:
       ↓
Get Previous Successful Version
       ↓
Save State as ROLLING_BACK
       ↓
Update Deployment Image Tag
       ↓
Monitor Rollout Progress
       ↓
Verify Health Post-Rollback
       ↓
If Success:
       ↓
Save State as ROLLED_BACK
       ↓
Record Successful Version
       ↓
If Failure:
       ↓
Save State as FAILED
       ↓
Request Manual Intervention
```

## Security Architecture

MANA follows security best practices:

### Authentication & Authorization
- **Kubernetes RBAC**: Least-privilege access to only necessary resources
  - Deployments: get, list, watch, update, patch
  - ReplicaSets: get, list, watch
  - Pods: get, list, watch
  - Events: create, patch
- **No External Access Required**: All communication happens within cluster
- **Secret Management**: Sensitive data (GitHub tokens) stored as Kubernetes secrets

### Container Security
- **Non-Root Execution**: Containers run as non-root users
- **Minimal Capabilities**: Linux capabilities dropped to minimum required
- **Resource Limits**: CPU and memory limits to prevent resource exhaustion
- **Read-Only Root Filesystem**: Where applicable
- **Security Contexts**: Properly configured for all containers

### Network Security
- **Service Exposure**: Services default to ClusterIP (internal only)
- **Ingress Control**: Ingress resources optional and configurable
- **Network Policies**: Recommended for production deployments
- **API Authentication**: Webhook endpoint can be secured with network policies

### Data Protection
- **Encryption at Rest**: Depends on underlying storage (ETCD, PVCs)
- **Secrets Encryption**: Kubernetes secrets can be encrypted at rest
- **Audit Log Integrity**: Append-only logging prevents tampering
- **Input Validation**: All external inputs validated and sanitized

## Scalability and Performance

### Horizontal Scaling
- **Stateless Design**: Orchestrator instances can be scaled horizontally
- **Shared State**: Redis provides shared state between instances
- **Leader Election**: Optional leader election to prevent duplicate processing
- **Partitioning**: Deployments can be partitioned by namespace or label

### Performance Characteristics
- **Low Latency**: Sub-second response times for API requests
- **Efficient Health Checks**: Concurrent health checking with timeouts
- **Batch Operations**: Where possible, operations are batched
- **Caching**: Strategic caching of frequently accessed data
- **Asynchronous Processing**: Non-blocking operations for external calls

### Resource Efficiency
- **Minimal Footprint**: Base installation requires minimal resources
- **Configurable Resource Requests**: Tunable for different scales
- **Efficient Data Structures**: Optimized use of Redis data types
- **Garbage Collection**: Automatic cleanup of old states via TTL

## Extensibility Points

MANA is designed to be extensible for different environments and requirements:

### Plugin Architecture
- **Failure Handlers**: Custom logic for different failure types
- **Notification Channels**: Extend beyond Slack/email to PagerDuty, Webhooks, etc.
- **Health Check Strategies**: Custom health check implementations
- **Storage Backends**: Alternative to Redis (though Redis is recommended)
- **CI/CD Integrations**: Beyond GitHub Actions to GitLab CI, Jenkins, etc.

### Configuration Options
- **Feature Flags**: Enable/disable specific functionality
- **Policy Engines**: Plug in custom decision-making logic
- **Metrics Exporters**: Additional monitoring backends
- **Logging Adapters**: Different log formats or destinations

### API Extensibility
- **Webhook Formats**: Support for different CI/CD webhook formats
- **Custom Endpoints**: Add organization-specific endpoints
- **GraphQL Interface**: Optional GraphQL API alongside REST
- **gRPC Support**: For high-performance internal communication

## Deployment Patterns

MANA supports various deployment patterns:

### Single Instance (Development/Testing)
- Simple deployment with one orchestrator pod
- Suitable for testing and low-traffic environments
- Uses embedded Redis or external instance

### High Availability (Production)
- Multiple orchestrator replicas (3+ recommended)
- External Redis cluster for HA
- Load balancing via Kubernetes Service
- Optional leader election to prevent conflicts

### Multi-Tenant
- Namespace isolation for different teams/environments
- Resource quotas to prevent noisy neighbors
- Separate configuration per tenant/namespace
- Shared monitoring and alerting

### Edge/Distributed
- Lightweight version for edge computing scenarios
- Reduced feature set for resource-constrained environments
- Local state persistence with periodic sync
- Optimized for intermittent connectivity

## Technology Stack

### Core Technologies
- **Language**: Python 3.9+
- **Framework**: FastAPI for API server
- **State Management**: Redis 6+
- **Orchestration**: Kubernetes client-go (via official Python client)
- **Webhook Processing**: Standard HTTP with signature validation
- **Audit Logging**: Structured JSON logging
- **Monitoring**: Prometheus client library
- **Containerization**: Docker with multi-stage builds

### Infrastructure
- **Container Orchestration**: Kubernetes 1.22+
- **Service Mesh**: Compatible with Istio, Linkerd, etc. (optional)
- **Service Discovery**: Kubernetes DNS
- **Load Balancing**: Kubernetes Services (ClusterIP, LoadBalancer, Ingress)
- **Storage**: Persistent Volumes for Redis (if using external persistence)
- **Networking**: Standard Kubernetes networking (CNI agnostic)

### Development & Testing
- **Testing Framework**: Pytest with asyncio support
- **Mocking**: unittest.mock for isolation
- **CI**: GitHub Actions for continuous integration
- **Code Quality**: Ruff, MyPy for linting and type checking
- **Documentation**: MkDocs with material theme
- **Version Control**: Git with conventional commits
- **Release Process**: Semantic versioning with automated changelogs

## Failure Modes and Recovery

MANA is designed to handle various failure scenarios gracefully:

### Orchestrator Failure
- **Detection**: Kubernetes liveness/readiness probes
- **Recovery**: Automatic restart by Kubernetes
- **State Safety**: All state persisted in Redis
- **Failover**: Multiple replicas ensure continued operation
- **Data Loss**: Minimal to none (state saved before external calls)

### Redis Failure
- **Detection**: Health checks in orchestrator
- **Degraded Mode**: Limited functionality (no state persistence)
- **Recovery**: Automatic restart or failover (if using Redis cluster)
- **Data Safety**: Persistence depends on Redis configuration
- **Manual Intervention**: May be required for data recovery

### Kubernetes API Failure
- **Detection**: Health checks and API call timeouts
- **Degraded Mode**: Limited to local state operations
- **Recovery**: Automatic when API server recovers
- **Operations Affected**: Rollback, health checking, state updates
- **Mitigation**: Caching of frequently accessed data

### Network Partition
- **Detection**: Health check failures and timeouts
- **Degraded Mode**: Local operations only
- **Recovery**: Automatic when network heals
- **Consistency**: Eventual consistency model
- **Conflict Resolution**: Last-write-wins with timestamps

### GitHub API Failure
- **Detection**: API call failures and timeouts
- **Degraded Mode**: Retry functionality disabled
- **Recovery**: Automatic when GitHub API recovers
- **Operations Affected**: Workflow re-runs for retry functionality
- **Mitigation**: Exponential backoff and circuit breaker patterns

## Extending MANA

### Adding Custom Failure Handlers
1. Create a new failure handler class inheriting from `BaseFailureHandler`
2. Implement the `handle_failure` method
3. Register the handler in the failure type mapping
4. Configure the orchestrator to use your custom handler

### Adding Notification Channels
1. Create a new notifier class inheriting from `BaseNotifier`
2. Implement the `send_notification` method
3. Register the notifier in the notification factory
4. Configure the orchestrator to use your notifier

### Adding Health Check Strategies
1. Create a new health check class inheriting from `BaseHealthChecker`
2. Implement the `check_health` method
3. Register the checker in the health check factory
4. Configure deployments to use your custom strategy

### Adding Storage Backends
1. Create a new storage class inheriting from `BaseStorage`
2. Implement the required interface methods
3. Register the storage in the storage factory
4. Configure the orchestrator to use your storage backend

## Conclusion

MANA's architecture balances simplicity with extensibility, providing a robust foundation for self-healing CI/CD pipelines while remaining adaptable to various environments and requirements. Its use of proven technologies (Redis, Kubernetes, FastAPI) ensures reliability and operational familiarity, while its modular design allows for customization and extension as needs evolve.

The system is designed to be observable, secure, and production-ready from the ground up, with careful attention to failure modes, scalability, and operational best practices.