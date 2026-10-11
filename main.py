from typing import Annotated

from fastapi import Cookie, FastAPI, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from auth import AUTH_COOKIE, AUTH_KEY

app = FastAPI()
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
def home(s3drive: Annotated[str | None, Cookie()] = None):
    if s3drive == AUTH_COOKIE:
        return RedirectResponse(url="/app", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url="/app", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/app", response_class=HTMLResponse)
def app_page(request: Request, s3drive: Annotated[str | None, Cookie()] = None):
    return templates.TemplateResponse(request=request, name="app.html")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, s3drive: Annotated[str | None, Cookie()] = None):
    return templates.TemplateResponse(request=request, name="login.html")


@app.post("/login")
def login(response: Response):
    response = RedirectResponse(url="/app", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(key=AUTH_KEY, value=AUTH_COOKIE)
    return response


@app.post("/logout")
def logout(response: Response):
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key=AUTH_KEY)
    return response
