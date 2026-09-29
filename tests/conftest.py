import copy
import json
from pathlib import Path

import pytest

from app.db import connect, init_db

FIXTURE = Path(__file__).parent / "fixtures" / "sample_calibration.json"


@pytest.fixture
def conn(tmp_path):
    """A fresh database in a temporary folder."""
    db = tmp_path / "test.db"
    init_db(db)
    c = connect(db)
    yield c
    c.close()


@pytest.fixture
def sample_data():
    """The made-up fixture file, loaded fresh for each test (safe to modify)."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def points_question(sample_data):
    return copy.deepcopy(sample_data["questions"][0])


@pytest.fixture
def lorms_question(sample_data):
    return copy.deepcopy(sample_data["questions"][1])
