"""Keep the pins of both components compatible so one virtualenv can hold them."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR = ROOT / "orchestrator" / "requirements-dev.txt"
SAMPLE_APP = ROOT / "sample-app" / "requirements.txt"
COMBINED = ROOT / "requirements-dev.txt"

pytestmark = pytest.mark.skipif(
    not (ORCHESTRATOR.is_file() and SAMPLE_APP.is_file()),
    reason="both components are not available in this checkout",
)

PIN = re.compile(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s;#]+)")


def pins(path: Path) -> dict[str, str]:
    """Pinned packages of a requirements file, following -r includes"""
    found: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line.startswith("-r "):
            found.update(pins((path.parent / line[3:].strip()).resolve()))
            continue
        match = PIN.match(line)
        if match:
            found[re.sub(r"[-_.]+", "-", match.group(1)).lower()] = match.group(2)
    return found


def test_shared_packages_are_pinned_to_the_same_version():
    orchestrator, sample_app = pins(ORCHESTRATOR), pins(SAMPLE_APP)

    conflicts = {
        name: (orchestrator[name], sample_app[name])
        for name in orchestrator.keys() & sample_app.keys()
        if orchestrator[name] != sample_app[name]
    }

    assert conflicts == {}


def test_pin_parser_reads_extras_and_includes():
    orchestrator = pins(ORCHESTRATOR)

    assert "uvicorn" in orchestrator
    assert "pytest" in orchestrator
    assert "requests" in orchestrator


@pytest.mark.skipif(not COMBINED.is_file(), reason="no combined requirements file")
def test_combined_file_covers_both_components():
    combined = pins(COMBINED)

    assert pins(ORCHESTRATOR).items() <= combined.items()
    assert pins(SAMPLE_APP).items() <= combined.items()
