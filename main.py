import uuid
from sqlite3 import Cursor
from typing import Annotated

from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from auth import AUTH_COOKIE, AUTH_KEY
from db import DB_URL, get_db, init_db
from s3 import download_file_obj, init_bucket, upload_file_obj

app = FastAPI()
templates = Jinja2Templates(directory="templates")

print("init S3")
init_bucket()
print("init db")
init_db(DB_URL)


@app.get("/", response_class=HTMLResponse)
async def home(s3drive: Annotated[str | None, Cookie()] = None):
    if s3drive == AUTH_COOKIE:
        return RedirectResponse(url="/app", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url="/app", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, s3drive: Annotated[str | None, Cookie()] = None):
    return templates.TemplateResponse(request=request, name="login.html")


@app.post("/login")
async def login(response: Response):
    response = RedirectResponse(url="/app", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(key=AUTH_KEY, value=AUTH_COOKIE)
    return response


@app.post("/logout")
async def logout(response: Response):
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key=AUTH_KEY)
    return response


@app.get("/app/{file_path:path}", response_class=HTMLResponse)
async def app_page(
    request: Request,
    file_path: str,
    db: Annotated[Cursor, Depends(get_db)],
    s3drive: Annotated[str | None, Cookie()] = None,
):
    if s3drive != AUTH_COOKIE:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    if file_path == "":
        db.execute(
            "SELECT id, name, is_file FROM files WHERE parent_id IS NULL ORDER BY name ASC"
        )
    else:
        split_file_path = file_path.split("/")
        last_path = split_file_path[-1]
        db.execute(
            "SELECT id, name, is_file FROM files WHERE parent_id = ?", (last_path,)
        )
    data = []
    for row in db.fetchall():
        print(row[1])
        data.append(
            {"id": row[0], "name": row[1], "type": "file" if row[2] else "folder"}
        )

    return templates.TemplateResponse(
        request=request,
        name="app.html",
        context={
            "request": request,
            "tree": data,
            "file_path": file_path,
        },
    )


@app.post("/app/create-folder/{file_path:path}")
async def app_create_folder(
    file_path: str,
    foldername: Annotated[str, Form()],
    db: Annotated[Cursor, Depends(get_db)],
    s3drive: Annotated[str | None, Cookie()] = None,
):
    split_path = file_path.split("/")
    parent_id = split_path[-1] if len(split_path) > 0 else None
    if parent_id is not None:
        parent_id = parent_id if parent_id.strip() != "" else None
    folder_id = uuid.uuid4()
    db.execute(
        "INSERT INTO files (id, name, parent_id, is_file) VALUES (?, ?, ?, ?)",
        (str(folder_id), foldername, parent_id, False),
    )
    return RedirectResponse(
        url=f"/app/{file_path}", status_code=status.HTTP_303_SEE_OTHER
    )


@app.get("/app/download/{file_id}")
async def app_download_file(
    file_id: str,
    db: Annotated[Cursor, Depends(get_db)],
    s3drive: Annotated[str | None, Cookie()] = None,
):
    if s3drive != AUTH_COOKIE:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    # check file on database
    db.execute(
        """
        SELECT id, object_storage_name, name
        FROM files
        WHERE id = ?
        """,
        (file_id,),
    )
    row = db.fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Files path not found"
        )

    # get file on s3
    response_file = download_file_obj(key=row[1])
    if response_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Files object not found"
        )

    return StreamingResponse(
        content=response_file["Body"].iter_chunks(),
        media_type=response_file.get("ContentType", "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{row[2]}"'},
    )


@app.post("/app/upload/")
async def app_upload_file(
    request: Request,
    db: Annotated[Cursor, Depends(get_db)],
    file: UploadFile,
    s3drive: Annotated[str | None, Cookie()] = None,
):
    if s3drive != AUTH_COOKIE:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    file_id = uuid.uuid4()
    object_name = f"{file.filename}-{file_id}"
    db.execute(
        """
        INSERT INTO files (id, name, object_storage_name, parent_id, is_file)
        VALUES (?, ?, ?, ?, ?)
        """,
        (str(file_id), file.filename, object_name, None, True),
    )
    upload_file_obj(file=file, file_id=file_id)
    return RedirectResponse(url="/app", status_code=status.HTTP_303_SEE_OTHER)
