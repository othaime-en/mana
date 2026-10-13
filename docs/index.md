# MANA - Self-Healing CI/CD Pipeline

Welcome to the MANA documentation! MANA is a Kubernetes-native orchestration system that automatically detects and recovers from deployment failures through intelligent retry and rollback mechanisms, reducing Mean Time To Recovery (MTTR) from minutes to seconds.

## What is MANA?

MANA (Self-Healing CI/CD Pipeline) is an open-source system designed to automate the recovery process when Kubernetes deployments fail. Instead of requiring manual intervention for routine failures, MANA:

- **Detects failures** in real-time through health checks and pod monitoring
- **Intelligently retries** failed deployments with exponential backoff
- **Automatically rolls back** to the last stable version when retries are exhausted
- **Integrates with CI/CD** systems like GitHub Actions for automated workflow re-runs
- **Provides complete observability** through metrics, audit logging, and health checks
- **Maintains a full audit trail** for compliance and debugging

## Key Features

- 🔄 **Automatic Rollback**: Rolls back to previous stable version after configurable retry threshold
- ⏱️ **Intelligent Retry**: Exponential backoff retry mechanism with configurable limits
- 🐳 **Kubernetes-Native**: Built specifically for Kubernetes with proper RBAC and resource management
- 🔧 **CI/CD Integration**: GitHub Actions webhook support for automatic workflow re-runs
- 📊 **Complete Observability**: Prometheus metrics, structured audit logging, and health checks
- 🛡️ **Production-Ready**: Secure by design with least-privilege RBAC, non-root containers, and secret management
- 📝 **Comprehensive Audit Trail**: JSON-formatted audit logs for easy parsing and compliance

## Getting Started

To get started with MANA, check out our [Getting Started Guide](getting-started/index.md).

## Documentation

- [Getting Started](getting-started/index.md) - Installation, quick start, and basic configuration
- [Architecture](architecture/overview.md) - System design, components, and data flow
- [Configuration](configuration/overview.md) - Configuring MANA for your environment
- [Usage](usage/basic-usage.md) - Basic usage, CI/CD integration, and monitoring
- [Reference](reference/api.md) - API reference, configuration reference, and CLI reference
- [Contributing](contributing/contributing.md) - How to contribute to the project
- [About](about/release-notes.md) - Release notes, license, and security information

## Community

- **Source Code**: [GitHub Repository](https://github.com/othaime-en/mana)
- **Issue Tracker**: [GitHub Issues](https://github.com/othaime-en/mana/issues)
- **License**: [MIT License](about/license.md)

## Support

If you encounter any issues or have questions, please:
1. Check the [Troubleshooting Guide](usage/troubleshooting.md)
2. Search existing [GitHub Issues](https://github.com/othaime-en/mana/issues)
3. Open a new issue if needed

---
*MANA is licensed under the MIT License - see the [LICENSE](about/license.md) file for details.*