"""
tests/conftest.py — M4a+

Pytest fixtures 共用:
- project_root:repo root path
- golden_data_dir:tests/golden_data/ path
- tmp_uploads:tmp uploads dir(每 test 一個獨立 dir)
- mongo_db:in-memory mongomock instance(若有裝 mongomock)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


# ============================================================
# Path fixtures
# ============================================================
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 把 project root 加進 sys.path 讓 test 能 import GenBI modules
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture(scope="session")
def project_root() -> Path:
    return _PROJECT_ROOT


@pytest.fixture(scope="session")
def golden_data_dir() -> Path:
    return _PROJECT_ROOT / "tests" / "golden_data"


@pytest.fixture
def tmp_uploads(tmp_path: Path) -> Path:
    """每 test 獨立 uploads dir(pytest 自動 cleanup)。"""
    d = tmp_path / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ============================================================
# MongoDB fixture(via mongomock if available)
# ============================================================
try:
    import mongomock
    _MONGOMOCK_OK = True
except ImportError:
    _MONGOMOCK_OK = False


@pytest.fixture
def mongo_db():
    """In-memory MongoDB(mongomock)。若沒裝 mongomock,該 test skip。

    使用方式:
        def test_xxx(mongo_db):
            repo = UploadRepository(mongo_db)
            ...
    """
    if not _MONGOMOCK_OK:
        pytest.skip("mongomock not installed — pip install -r requirements-dev.txt")
    client = mongomock.MongoClient()
    db = client["test_genbi"]
    yield db
    # cleanup:drop all collections
    for coll in db.list_collection_names():
        db[coll].drop()


# ============================================================
# Mark helpers
# ============================================================
def pytest_collection_modifyitems(config, items):
    """對 marker 'requires_mongo' 的 test,沒裝 mongomock 自動 skip。"""
    if not _MONGOMOCK_OK:
        skip_mongo = pytest.mark.skip(reason="mongomock not installed")
        for item in items:
            if "requires_mongo" in item.keywords:
                item.add_marker(skip_mongo)
