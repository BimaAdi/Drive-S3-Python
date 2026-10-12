import uuid
from sqlite3 import Cursor
from typing import Annotated

import boto3
from fastapi import Cookie, Depends, FastAPI, Request, Response, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from auth import AUTH_COOKIE, AUTH_KEY, BUCKET, S3_ACCESS, S3_SECRET
from db import get_db, init_db

app = FastAPI()
templates = Jinja2Templates(directory="templates")

print("init S3")
s3 = boto3.client(
    "s3",
    endpoint_url="http://localhost:9000",
    aws_access_key_id=S3_ACCESS,
    aws_secret_access_key=S3_SECRET,
)
try:
    s3.create_bucket(Bucket=BUCKET)
    print(f"bucket {BUCKET} succesfully created")
except s3.exceptions.BucketAlreadyOwnedByYou:
    print(f"bucket {BUCKET} already created")

print("init db")
init_db()


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


@app.get("/app", response_class=HTMLResponse)
async def app_page(
    request: Request,
    db: Annotated[Cursor, Depends(get_db)],
    s3drive: Annotated[str | None, Cookie()] = None,
):
    if s3drive != AUTH_COOKIE:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    db.execute("SELECT id, name, is_file FROM files ORDER BY name ASC")
    data = []
    for row in db.fetchall():
        data.append(
            {"id": row[0], "name": row[1], "type": "file" if row[2] else "folder"}
        )
    
    return templates.TemplateResponse(
        request=request,
        name="app.html",
        context={
            "request": request,
            "tree": data,
        },
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
    s3.upload_fileobj(
        file.file, BUCKET, object_name, ExtraArgs={"ContentType": file.content_type}
    )
    return RedirectResponse(url="/app", status_code=status.HTTP_303_SEE_OTHER)
