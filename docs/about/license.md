# License

MANA is released under the **MIT License**. The same text is in the [`LICENSE`](https://github.com/othaime-en/mana/blob/main/LICENSE) file at the root of the repository.

```text
MIT License

Copyright (c) 2025 othaime-en

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

## What This Means in Practice

This is a plain-language summary, not legal advice.

**You may**

- Use MANA for personal, internal or commercial purposes
- Modify it and build derivative works
- Distribute it, with or without changes
- Sublicense it as part of a larger work, including a closed-source one

**You must**

- Include the copyright notice and the licence text in all copies or substantial portions of the software

**You get**

- No warranty and no liability from the authors. The software is provided "as is". Because MANA changes live Kubernetes Deployments, test it in a non-production environment before relying on it.

## Contributions

By submitting a pull request you agree that your contribution is licensed under the same MIT License (see [Contributing](../contributing/contributing.md)).

## Third-Party Software

MANA depends on open-source packages and container images that carry their own licences. Review them for your use case.

| Component                                                         | Used for                  | Source                                 |
| ----------------------------------------------------------------- | ------------------------- | -------------------------------------- |
| FastAPI, Uvicorn, Pydantic                                        | Orchestrator API          | PyPI (`orchestrator/requirements.txt`) |
| redis-py, kubernetes (Python client), requests, prometheus-client | Orchestrator integrations | PyPI                                   |
| Flask, Gunicorn, prometheus-flask-exporter, python-json-logger    | Sample app                | PyPI (`sample-app/requirements.txt`)   |
| Redis                                                             | State store               | `redis:8-alpine` image                 |
| Prometheus, Grafana, Loki, Alloy, Alertmanager                   | Monitoring                | Container images and Helm charts       |
| Terraform providers (`kubernetes`, `helm`)                        | Infrastructure            | Terraform registry                     |

To list the exact licences of the Python dependencies you have installed:

```bash
pip install pip-licenses
pip-licenses --from=mixed --format=markdown
```

Note that some monitoring components (for example Grafana and Loki) are licensed under the AGPL-3.0. Running them unmodified as separate services does not change MANA's MIT licence, but check their terms if you modify or redistribute them.

## Documentation Licence

Unless stated otherwise, this documentation is provided under the same MIT License as the code. The [Code of Conduct](../contributing/code-of-conduct.md) is adapted from the Contributor Covenant under CC BY 4.0.
