import os
import sys
import tempfile
from pathlib import Path

import pytest

# phải đặt trước khi import app: không chạy luồng dịch nền, DB tạm
os.environ["WORKER"] = "0"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="truyen-test-")
os.environ["TRANSLATOR"] = "mock"
os.environ["FETCH_DELAY"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import migrate  # noqa: E402
from app.main import app  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fresh_db(tmp_path):
    """Mỗi test một DB riêng."""
    settings.data_dir = tmp_path
    settings.ensure()
    migrate()
    yield tmp_path


@pytest.fixture
def client(fresh_db):
    with TestClient(app) as c:
        yield c


def csrf_of(client):
    if "csrf" not in client.cookies:
        client.get("/dang-nhap")
    return client.cookies.get("csrf")


def register(client, username, password="matkhau123", display=""):
    tok = csrf_of(client)
    return client.post("/dang-ky", data={"csrf": tok, "username": username, "display_name": display or username,
                                          "password": password, "password2": password}, follow_redirects=False)
