# MANA - Self-Healing CI/CD Pipeline

MANA is a Kubernetes-native orchestration system that automatically detects and recovers from deployment failures through intelligent retry and rollback mechanisms, reducing Mean Time To Recovery (MTTR) from minutes to seconds.

## Problem

Modern deployment failures require manual intervention, leading to extended downtime and on-call fatigue:
- Manual rollbacks take 10-15 minutes
- Failed deployments cause 20+ minutes of downtime
- Engineers wake up at night for routine failures
- Inconsistent recovery procedures across teams

## Solution

MANA provides automated deployment recovery through:
- Real-time failure detection via health checks and pod monitoring
- Intelligent decision engine with configurable retry/rollback policies
- Automatic rollback to last stable version
- Complete observability through Prometheus and Grafana

## Architecture

The system consists of:
- **Sample Application**: Flask-based deployment target with health endpoints
- **Orchestrator**: FastAPI service with failure detection and decision engine
- **State Management**: Redis-backed deployment history and retry tracking
- **Kubernetes Integration**: Direct K8s API interaction for rollback execution
- **Monitoring Stack**: Prometheus, Grafana, and Loki for metrics and logs
- **CI/CD Integration**: GitHub Actions webhook integration

## Key Features

- Automatic rollback to previous stable version on failure
- Configurable retry attempts before rollback
- Multi-environment support (production, staging, canary)
- Real-time health monitoring
- GitHub Actions integration
- Complete audit trail of recovery actions
- RBAC-compliant Kubernetes operations


## Why I Built This

I built MANA to deeply understand distributed systems design and Kubernetes operator patterns. While tools like Argo Rollouts provide similar functionality, building from scratch helped me master:
- State management in distributed systems
- Webhook-based integrations and event processing
- Kubernetes API interactions and RBAC
- Production-grade observability patterns
- FastAPI async programming and Redis integration

## License

MIT
