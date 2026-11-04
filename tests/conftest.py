from pathlib import Path
import uuid
import os
import pytest


@pytest.fixture
def tmp_path():
    # Keep Windows sandbox fixtures in the writable workspace with ordinary ACLs.
    path = Path(os.getenv("DIAMOND_TEST_DIR", str(Path(__file__).resolve().parents[1] / ".diamond" / "tests"))) / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path
