import pytest

from src.utils import ImageReference, split_image_reference, with_tag

DIGEST = "sha256:" + "a" * 64


@pytest.mark.parametrize("image,expected", [
    ("sample-app", ImageReference("sample-app", None, None)),
    ("sample-app:1.0.0", ImageReference("sample-app", "1.0.0", None)),
    ("ghcr.io/o/r/sample-app:v1a2b3c4", ImageReference("ghcr.io/o/r/sample-app", "v1a2b3c4", None)),
    ("localhost:5000/sample-app", ImageReference("localhost:5000/sample-app", None, None)),
    ("localhost:5000/sample-app:1.0.0", ImageReference("localhost:5000/sample-app", "1.0.0", None)),
    (f"sample-app@{DIGEST}", ImageReference("sample-app", None, DIGEST)),
    (f"localhost:5000/sample-app:1.0.0@{DIGEST}", ImageReference("localhost:5000/sample-app", "1.0.0", DIGEST)),
])
def test_split_image_reference(image, expected):
    assert split_image_reference(image) == expected


@pytest.mark.parametrize("image,expected", [
    ("sample-app:1.0.0", "sample-app:0.9.0"),
    ("localhost:5000/sample-app", "localhost:5000/sample-app:0.9.0"),
    ("localhost:5000/sample-app:1.0.0", "localhost:5000/sample-app:0.9.0"),
    (f"ghcr.io/o/sample-app@{DIGEST}", "ghcr.io/o/sample-app:0.9.0"),
])
def test_with_tag_replaces_tag_and_drops_digest(image, expected):
    assert with_tag(image, "0.9.0") == expected
