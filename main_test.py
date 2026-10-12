"""Tests for the Drive-S3 FastAPI application.

S3 access is mocked with pytest-mock (``mocker``) and SQLite is swapped for a
per-test in-memory database through FastAPI dependency overrides.

``main`` is imported lazily so that ``mocker`` can patch the module-level
``s3.init_bucket()`` call before the import runs.  Every test gets its own
database, so the suite is safe to run in parallel (``pytest -n auto``).
"""

import asyncio
import sqlite3
import uuid

import pytest
from fastapi import HTTPException, status
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient

from auth import AUTH_COOKIE, AUTH_KEY
from db import get_db, init_db

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def app_module(mocker):
    """Import the application once, with its import-time S3 call mocked out.

    ``main`` runs ``init_bucket()`` (which talks to S3) while being imported, so
    the mock has to be installed before the import happens.  Python caches the
    module, so the import below only runs on the first test of a worker.
    """
    mocker.patch("s3.init_bucket")
    import main

    return main


@pytest.fixture
def db_conn():
    """An isolated in-memory SQLite DB, schema created by ``db.init_db``."""
    # A named, shared-cache in-memory database lets ``init_db`` (which opens its
    # own connection) and the request connection talk to the same database.
    db_url = f"file:test_{uuid.uuid4().hex}?mode=memory&cache=shared"
    conn = sqlite3.connect(db_url, uri=True, check_same_thread=False)
    init_db(db_url)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def client(app_module, db_conn):
    """A TestClient whose ``get_db`` dependency points at ``db_conn``."""

    def override_get_db():
        try:
            yield db_conn.cursor()
        finally:
            # ``db.get_db`` commits when the request finishes.
            db_conn.commit()

    app_module.app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app_module.app) as test_client:
            yield test_client
    finally:
        app_module.app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def auth_client(client):
    """A TestClient carrying a valid auth cookie."""
    client.cookies.set(AUTH_KEY, AUTH_COOKIE)
    return client


def _insert_file(
    conn: sqlite3.Connection,
    name: str,
    *,
    object_storage_name: str | None = None,
    parent_id: str | None = None,
    is_file: bool = True,
) -> str:
    file_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO files (id, name, object_storage_name, parent_id, is_file)
        VALUES (?, ?, ?, ?, ?)
        """,
        (file_id, name, object_storage_name, parent_id, is_file),
    )
    conn.commit()
    return file_id


async def _read_stream(response) -> bytes:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.encode() if isinstance(chunk, str) else chunk)
    return b"".join(chunks)


# --------------------------------------------------------------------------- #
# GET /
# --------------------------------------------------------------------------- #


def test_home_redirects_to_app(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/app"

    client.cookies.set(AUTH_KEY, AUTH_COOKIE)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/app"


# --------------------------------------------------------------------------- #
# GET /login
# --------------------------------------------------------------------------- #


def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"].startswith("text/html")
    assert "Login Page" in response.text


# --------------------------------------------------------------------------- #
# POST /login
# --------------------------------------------------------------------------- #


def test_login_sets_cookie_and_redirects(client):
    response = client.post("/login", follow_redirects=False)
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/app"
    assert response.cookies.get(AUTH_KEY) == AUTH_COOKIE


# --------------------------------------------------------------------------- #
# POST /logout
# --------------------------------------------------------------------------- #


def test_logout_clears_cookie_and_redirects(auth_client):
    response = auth_client.post("/logout", follow_redirects=False)
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/login"
    assert f'{AUTH_KEY}=""' in response.headers.get("set-cookie", "")


# --------------------------------------------------------------------------- #
# GET /app/{file_path:path}
# --------------------------------------------------------------------------- #


def test_app_page_redirects_when_unauthenticated(client):
    response = client.get("/app/", follow_redirects=False)
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/login"


def test_app_page_lists_root_entries(auth_client, db_conn):
    _insert_file(db_conn, "docs", is_file=False)
    _insert_file(db_conn, "photo.png", object_storage_name="photo.png-x", is_file=True)

    response = auth_client.get("/app/")

    assert response.status_code == status.HTTP_200_OK
    assert "docs" in response.text
    assert "photo.png" in response.text


def test_app_page_lists_nested_entries(auth_client, db_conn):
    parent_id = _insert_file(db_conn, "docs", is_file=False)
    _insert_file(
        db_conn, "notes.txt", object_storage_name="notes.txt-x", parent_id=parent_id
    )
    _insert_file(db_conn, "other.txt", object_storage_name="other.txt-x")

    response = auth_client.get(f"/app/{parent_id}")

    assert response.status_code == status.HTTP_200_OK
    assert "notes.txt" in response.text
    assert "other.txt" not in response.text


def test_app_page_empty_folder(auth_client, db_conn):
    response = auth_client.get("/app/")

    assert response.status_code == status.HTTP_200_OK
    assert "folder is empty" in response.text


def test_app_page_hides_parent_link_at_root(auth_client, db_conn):
    response = auth_client.get("/app/")

    assert response.status_code == status.HTTP_200_OK
    assert "../" not in response.text


def test_app_page_parent_link_points_to_root_for_top_level_folder(auth_client, db_conn):
    folder_id = _insert_file(db_conn, "docs", is_file=False)

    response = auth_client.get(f"/app/{folder_id}")

    assert response.status_code == status.HTTP_200_OK
    assert "../" in response.text
    assert 'href="/app/"' in response.text


def test_app_page_parent_link_points_to_parent_folder(auth_client, db_conn):
    parent_id = _insert_file(db_conn, "docs", is_file=False)
    child_id = _insert_file(db_conn, "nested", parent_id=parent_id, is_file=False)

    response = auth_client.get(f"/app/{child_id}")

    assert response.status_code == status.HTTP_200_OK
    assert "../" in response.text
    assert f'href="/app/{parent_id}"' in response.text


# --------------------------------------------------------------------------- #
# POST /app/create-folder/{file_path:path}
# --------------------------------------------------------------------------- #


def test_app_create_folder_at_root(auth_client, db_conn):
    response = auth_client.post(
        "/app/create-folder/",
        data={"foldername": "new-folder"},
        follow_redirects=False,
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/app/"

    row = db_conn.execute(
        "SELECT name, parent_id, is_file FROM files WHERE name = ?", ("new-folder",)
    ).fetchone()
    assert row is not None
    assert row[1] is None
    assert row[2] == 0


def test_app_create_folder_nested(auth_client, db_conn):
    parent_id = _insert_file(db_conn, "docs", is_file=False)

    response = auth_client.post(
        f"/app/create-folder/{parent_id}",
        data={"foldername": "child"},
        follow_redirects=False,
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == f"/app/{parent_id}"

    row = db_conn.execute(
        "SELECT parent_id FROM files WHERE name = ?", ("child",)
    ).fetchone()
    assert row is not None
    assert row[0] == parent_id


# --------------------------------------------------------------------------- #
# GET /app/download/{file_id}
#
# NOTE: ``/app/{file_path:path}`` is registered first and matches every
# ``/app/...`` GET, so this route is shadowed in the running app.  The handler
# is exercised directly to still cover its logic.
# --------------------------------------------------------------------------- #


def test_app_download_file_streams_object(mocker, app_module, db_conn):
    object_name = "hello.txt-x"
    file_id = _insert_file(db_conn, "hello.txt", object_storage_name=object_name)

    body = mocker.MagicMock()
    body.iter_chunks.return_value = [b"hello world"]
    download = mocker.patch(
        "main.download_file_obj",
        return_value={"Body": body, "ContentType": "text/plain"},
    )

    response = asyncio.run(
        app_module.app_download_file(file_id, db=db_conn.cursor(), s3drive=AUTH_COOKIE)
    )

    download.assert_called_once_with(key=object_name)
    assert response.status_code == status.HTTP_200_OK
    assert response.media_type == "text/plain"
    assert response.headers["content-disposition"] == 'attachment; filename="hello.txt"'
    assert asyncio.run(_read_stream(response)) == b"hello world"


def test_app_download_file_redirects_when_unauthenticated(mocker, app_module, db_conn):
    download = mocker.patch("main.download_file_obj")

    response = asyncio.run(
        app_module.app_download_file(
            str(uuid.uuid4()), db=db_conn.cursor(), s3drive=None
        )
    )

    download.assert_not_called()
    assert isinstance(response, RedirectResponse)
    assert response.headers["location"] == "/login"


def test_app_download_file_missing_in_db(mocker, app_module, db_conn):
    download = mocker.patch("main.download_file_obj")

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            app_module.app_download_file(
                str(uuid.uuid4()), db=db_conn.cursor(), s3drive=AUTH_COOKIE
            )
        )

    download.assert_not_called()
    assert excinfo.value.status_code == status.HTTP_404_NOT_FOUND


def test_app_download_file_missing_in_s3(mocker, app_module, db_conn):
    file_id = _insert_file(db_conn, "ghost.txt", object_storage_name="ghost.txt-x")
    download = mocker.patch("main.download_file_obj", return_value=None)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            app_module.app_download_file(
                file_id, db=db_conn.cursor(), s3drive=AUTH_COOKIE
            )
        )

    download.assert_called_once_with(key="ghost.txt-x")
    assert excinfo.value.status_code == status.HTTP_404_NOT_FOUND


# --------------------------------------------------------------------------- #
# POST /app/upload/
# --------------------------------------------------------------------------- #


def test_app_upload_file_stores_object_and_row(mocker, auth_client, db_conn):
    upload = mocker.patch("main.upload_file_obj")

    response = auth_client.post(
        "/app/upload/",
        files={"file": ("hello.txt", b"hello world", "text/plain")},
        follow_redirects=False,
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/app"

    row = db_conn.execute(
        "SELECT name, object_storage_name, is_file FROM files WHERE name = ?",
        ("hello.txt",),
    ).fetchone()
    assert row is not None
    assert row[2] == 1

    upload.assert_called_once()
    uploaded_file = upload.call_args.kwargs["file"]
    uploaded_id = upload.call_args.kwargs["file_id"]
    assert uploaded_file.filename == "hello.txt"
    assert row[1] == f"hello.txt-{uploaded_id}"


def test_app_upload_file_redirects_when_unauthenticated(mocker, client, db_conn):
    upload = mocker.patch("main.upload_file_obj")

    response = client.post(
        "/app/upload/",
        files={"file": ("hello.txt", b"hello world", "text/plain")},
        follow_redirects=False,
    )

    upload.assert_not_called()
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/login"
    assert db_conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# POST /app/delete/{file_path:path}
# --------------------------------------------------------------------------- #


def test_app_delete_file_removes_row_and_object(mocker, auth_client, db_conn):
    file_id = _insert_file(db_conn, "hello.txt", object_storage_name="hello.txt-x")
    delete = mocker.patch("main.delete_file_obj")

    response = auth_client.post(
        "/app/delete/",
        data={"file_id": file_id},
        follow_redirects=False,
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/app/"

    delete.assert_called_once_with(key="hello.txt-x")
    assert (
        db_conn.execute(
            "SELECT COUNT(*) FROM files WHERE id = ?", (file_id,)
        ).fetchone()[0]
        == 0
    )


def test_app_delete_file_redirects_to_current_folder(mocker, auth_client, db_conn):
    folder_id = _insert_file(db_conn, "docs", is_file=False)
    file_id = _insert_file(
        db_conn,
        "notes.txt",
        object_storage_name="notes.txt-x",
        parent_id=folder_id,
    )
    delete = mocker.patch("main.delete_file_obj")

    response = auth_client.post(
        f"/app/delete/{folder_id}",
        data={"file_id": file_id},
        follow_redirects=False,
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == f"/app/{folder_id}"
    delete.assert_called_once_with(key="notes.txt-x")


def test_app_delete_file_redirects_when_unauthenticated(mocker, client, db_conn):
    file_id = _insert_file(db_conn, "hello.txt", object_storage_name="hello.txt-x")
    delete = mocker.patch("main.delete_file_obj")

    response = client.post(
        "/app/delete/",
        data={"file_id": file_id},
        follow_redirects=False,
    )

    delete.assert_not_called()
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/login"
    assert (
        db_conn.execute(
            "SELECT COUNT(*) FROM files WHERE id = ?", (file_id,)
        ).fetchone()[0]
        == 1
    )


def test_app_delete_file_missing_in_db(mocker, auth_client, db_conn):
    delete = mocker.patch("main.delete_file_obj")

    response = auth_client.post(
        "/app/delete/",
        data={"file_id": str(uuid.uuid4())},
        follow_redirects=False,
    )

    delete.assert_not_called()
    assert response.status_code == status.HTTP_404_NOT_FOUND
