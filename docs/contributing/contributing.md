# Contributing

Thank you for your interest in MANA. Contributions of all sizes are welcome: bug reports, documentation fixes, tests, and new features. This guide explains how to propose and land changes.

By participating you agree to follow the [Code of Conduct](code-of-conduct.md). Contributions are licensed under the project's [MIT License](../about/license.md).

## Ways to Contribute

- **Report a bug** or unexpected behaviour.
- **Fix a known issue.** The [Data Flow](../architecture/data-flow.md#known-behaviours-and-limitations) page lists confirmed problems that are good starting points: the `/metrics/deployment/...` route shadowed by the Prometheus mount, and metrics that alerts reference but the orchestrator does not emit.
- **Improve tests.** The orchestrator's API layer (`api.py`) has no tests and overall coverage is under 50%.
- **Improve the docs.** Corrections, clearer examples and missing sections are all valuable.
- **Propose a feature** such as webhook authentication, a Redis password option, or adapters for other CI systems.

## Before You Start

1. Search [existing issues](https://github.com/othaime-en/mana/issues) and pull requests to avoid duplicating work.
2. For anything larger than a small fix, open an issue first describing the problem and your proposed approach, so design feedback comes before code.
3. Set up your environment with the [Development Setup](development-setup.md) guide.

## Reporting Bugs

Open an issue with:

- What you expected and what happened instead.
- Steps to reproduce, ideally a `curl` command or a failing test.
- Versions: MANA commit or image tag, Kubernetes version, how it was deployed (Compose, manifests, Terraform).
- Relevant orchestrator logs (`kubectl logs -n orchestrator -l app=orchestrator`) with secrets and tokens removed.
- The output of `curl $MANA/config` and `curl $MANA/health` if relevant.

**Security issues**: do not open a public issue. Follow the [Security policy](../about/security.md#reporting-a-vulnerability).

## Workflow

```bash
# 1. Fork on GitHub, then:
git clone https://github.com/<you>/mana.git && cd mana
git remote add upstream https://github.com/othaime-en/mana.git

# 2. Branch from main
git checkout -b fix/carry-workflow-run-id

# 3. Make changes, add tests, run checks
make test
make lint

# 4. Commit, push, open a pull request against main
git push -u origin fix/carry-workflow-run-id
```

Keep pull requests focused: one logical change per PR makes review faster.

### Branch names

Use a short prefix: `feat/`, `fix/`, `docs/`, `test/`, `chore/`, `refactor/`.

### Commit messages

The history follows [Conventional Commits](https://www.conventionalcommits.org/) style:

```text
feat(orchestrator): copy webhook metadata into new deployment state
fix: reject unknown failure types with 422
docs: document the rollback decision rule
test: cover manual rollback endpoint
chore: bump prometheus-client
```

Write the subject in the imperative mood, under about 72 characters, and explain the _why_ in the body when it is not obvious.

## Pull Request Checklist

- [ ] The change is focused and the description explains what and why, linking the issue (`Fixes #123`).
- [ ] New behaviour has tests; bug fixes include a test that fails without the fix.
- [ ] `make test` passes (apart from the two known failing orchestrator tests until they are fixed).
- [ ] `black`, `flake8` and `isort` are clean for changed Python files.
- [ ] Docs are updated: any change to endpoints, environment variables, defaults, metrics, or workflow behaviour updates the matching page under `docs/`.
- [ ] No secrets, tokens, kubeconfigs, `.env` files or Terraform state are committed.
- [ ] Kubernetes manifests keep their security settings (non-root, dropped capabilities, resource limits).

## Coding Standards

### Python

- Target Python 3.12; format with `black`, sort imports with `isort`, keep lines within the repository's lint limit (100 for CI `flake8`).
- Add type hints to new functions and use dataclasses or Pydantic models for structured data.
- Raise and catch **specific** exceptions; avoid bare `except:` in new code.
- Log with the `logging` module, never `print`. Never log tokens or full request bodies that may contain secrets.
- Emit an audit event (`AuditLogger`) for any new action that changes cluster or deployment state.
- Prefer small, testable functions. Comment only where the code is not self-explanatory.

### Orchestrator behaviour changes

Because the orchestrator can modify production Deployments, changes to decision logic need extra care:

- Add or update unit tests in `orchestrator/tests/test_orchestrator.py` covering each branch (retry, rollback, alert).
- Keep operations idempotent where possible; webhooks can be delivered twice.
- Update [Data Flow](../architecture/data-flow.md) and the configuration pages if defaults or semantics change.

### Kubernetes and Terraform

- Every workload manifest needs resource requests and limits, probes, a non-root `securityContext`, and labels (`app`, `version`).
- Run `terraform fmt -recursive` and `terraform validate`; do not commit `.tfstate`.
- Keep `kubernetes/` manifests and the Terraform `security` module consistent, or note the difference.

### Documentation

- Document what the code does today. Mark planned features clearly as planned.
- Use sentence-case headings and fenced code blocks with a language tag.
- Verify commands and outputs before including them. Run `mkdocs build --strict`.

### Workflows

- Pin third-party actions to a version, avoid `@master` for new additions, and never echo secrets.
- Keep the webhook contract in sync with `WebhookPayload` and `FailureType`.

## Review Process

This is a small project maintained in spare time, so reviews may take a while. Expect feedback on tests, naming and scope. Push follow-up commits to the same branch; there is no need to open a new PR. Once approved and CI is green, a maintainer merges the PR (the project uses merge commits for pull requests).

## Recognition

All contributors appear in the repository's commit history and the GitHub contributors graph.

## Questions

Open a [GitHub issue](https://github.com/othaime-en/mana/issues) describing your question. Please check the [Troubleshooting Guide](../usage/troubleshooting.md) first.
