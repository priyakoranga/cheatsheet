import re
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature
from sqlalchemy import func, inspect, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from starlette.middleware.sessions import SessionMiddleware

from . import config
from .database import Base, SessionLocal, engine, get_db
from .mailer import read_token, send_verification
from .models import Cheatsheet, User
from .ratelimit import client_ip, rate_limit
from .security import check_csrf, csrf_token, hash_password, verify_password

Base.metadata.create_all(engine)


def migrate():
    """Add new columns to an existing database. Existing users count as verified."""
    cols = {c["name"] for c in inspect(engine).get_columns("users")}
    if "email_verified" not in cols:
        default = "TRUE" if engine.dialect.name == "postgresql" else "1"
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT {default}"))


migrate()


def promote_admin():
    if not config.ADMIN_EMAIL:
        return
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.email == config.ADMIN_EMAIL))
        if u and not u.is_admin:
            u.is_admin = True
            db.commit()


promote_admin()

app = FastAPI(title="CheatSheet Hub")
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    https_only=config.COOKIE_SECURE,
    same_site="lax",
    max_age=14 * 24 * 3600,
)
BASE = Path(__file__).parent
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,30}$")


# ---------- helpers ----------
def current_user(request: Request, db: Session) -> User | None:
    uid = request.session.get("user_id")
    return db.get(User, uid) if uid else None


def render(request: Request, db: Session, name: str, status: int = 200, **ctx):
    user = current_user(request, db)
    if user and user.is_admin:
        ctx["pending_count"] = db.scalar(
            select(func.count()).select_from(Cheatsheet).where(Cheatsheet.status == "pending")
        )
    ctx.update(
        user=user,
        csrf=csrf_token(request),
        flash=request.session.pop("flash", None),
        categories=config.CATEGORIES,
    )
    return templates.TemplateResponse(request, name, ctx, status_code=status)


def safe_next(url: str) -> str:
    return url if url.startswith("/") and not url.startswith("//") else "/"


def flash(request: Request, msg: str):
    request.session["flash"] = msg


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


templates.env.filters["filesize"] = human_size


def list_sheets(request, db, template, owner_id=None, title="Cheatsheets"):
    q = request.query_params.get("q", "").strip()
    category = request.query_params.get("category", "")
    raw_page = request.query_params.get("page", "1")
    page = max(int(raw_page), 1) if raw_page.isdigit() else 1

    stmt = select(Cheatsheet).options(joinedload(Cheatsheet.owner))
    if owner_id:
        stmt = stmt.where(Cheatsheet.owner_id == owner_id)
    else:
        stmt = stmt.where(Cheatsheet.status == "approved")
    if category in config.CATEGORIES:
        stmt = stmt.where(Cheatsheet.category == category)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Cheatsheet.title.ilike(like), Cheatsheet.description.ilike(like)))

    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    pages = max((total + config.PAGE_SIZE - 1) // config.PAGE_SIZE, 1)
    page = min(page, pages)
    sheets = db.scalars(
        stmt.order_by(Cheatsheet.created_at.desc())
        .limit(config.PAGE_SIZE)
        .offset((page - 1) * config.PAGE_SIZE)
    ).all()
    return render(
        request, db, template,
        sheets=sheets, q=q, category=category, page=page, pages=pages, total=total, title=title,
    )


# ---------- pages ----------
@app.get("/")
def index(request: Request, db: Session = Depends(get_db)):
    return list_sheets(request, db, "index.html")


@app.get("/mine")
def mine(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", 303)
    return list_sheets(request, db, "index.html", owner_id=user.id, title="My uploads")


@app.get("/register")
def register_form(request: Request, db: Session = Depends(get_db)):
    return render(request, db, "register.html", next=request.query_params.get("next", ""))


@app.post("/register")
def register(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    csrf: str = Form(""),
    next: str = Form(""),
):
    check_csrf(request, csrf)
    rate_limit(f"reg:{client_ip(request)}", 5, 3600)
    email = email.strip().lower()
    username = username.strip()
    error = None
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or len(email) > 255:
        error = "Enter a valid email."
    elif not USERNAME_RE.match(username):
        error = "Username: 3-30 letters, numbers or underscore."
    elif len(password) < 8 or len(password) > 128:
        error = "Password must be 8-128 characters."
    if error:
        return render(request, db, "register.html", status=400, error=error, email=email, username=username, next=next)

    user = User(email=email, username=username, password_hash=hash_password(password),
                is_admin=bool(config.ADMIN_EMAIL) and email == config.ADMIN_EMAIL,
                email_verified=not config.EMAIL_VERIFICATION_ENABLED)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(request, db, "register.html", status=400,
                      error="Email or username already taken.", email=email, username=username, next=next)
    if config.EMAIL_VERIFICATION_ENABLED:
        sent = send_verification(user)
        flash(request, "Account created! Check your email for the verification link." if sent else
              "Account created, but the email failed to send. Use 'Resend verification' below.")
        return RedirectResponse("/login", 303)
    request.session.clear()
    request.session["user_id"] = user.id
    flash(request, "Welcome! Your account is ready.")
    return RedirectResponse(safe_next(next), 303)


@app.get("/login")
def login_form(request: Request, db: Session = Depends(get_db)):
    return render(request, db, "login.html", next=request.query_params.get("next", ""))


@app.post("/login")
def login(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
    csrf: str = Form(""),
    next: str = Form(""),
):
    check_csrf(request, csrf)
    email = email.strip().lower()
    rate_limit(f"login:{client_ip(request)}", 20, 300)
    rate_limit(f"loginmail:{email}", 8, 900)
    user = db.scalar(select(User).where(User.email == email))
    if not user or not verify_password(password, user.password_hash):
        return render(request, db, "login.html", status=400, error="Invalid email or password.", next=next)
    if not user.email_verified:
        return render(request, db, "login.html", status=403, next=next,
                      error="Please verify your email first. Check your inbox or use 'Resend verification'.")
    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse(safe_next(next), 303)


@app.get("/verify/{token}")
def verify_email(token: str, request: Request, db: Session = Depends(get_db)):
    try:
        data = read_token(token)
        user = db.get(User, data["uid"])
        if not user or user.email != data["e"]:
            raise BadSignature("mismatch")
    except (BadSignature, KeyError):
        return render(request, db, "resend.html", status=400,
                      error="That link is invalid or expired. Request a new one below.")
    user.email_verified = True
    db.commit()
    flash(request, "Email verified! You can log in now.")
    return RedirectResponse("/login", 303)


@app.get("/resend")
def resend_form(request: Request, db: Session = Depends(get_db)):
    return render(request, db, "resend.html")


@app.post("/resend")
def resend(request: Request, db: Session = Depends(get_db), email: str = Form(...), csrf: str = Form("")):
    check_csrf(request, csrf)
    email = email.strip().lower()
    rate_limit(f"resend:{client_ip(request)}", 5, 3600)
    rate_limit(f"resendmail:{email}", 3, 3600)
    user = db.scalar(select(User).where(User.email == email))
    if user and not user.email_verified and config.EMAIL_VERIFICATION_ENABLED:
        send_verification(user)
    flash(request, "If that account exists and isn't verified, a new link is on its way.")
    return RedirectResponse("/login", 303)


@app.post("/logout")
def logout(request: Request, csrf: str = Form("")):
    check_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/", 303)


@app.get("/upload")
def upload_form(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login?next=/upload", 303)
    if not user.is_admin:
        raise HTTPException(403, "Only the admin can upload")
    return render(request, db, "upload.html")


@app.post("/upload")
async def upload(
    request: Request,
    db: Session = Depends(get_db),
    title: str = Form(...),
    description: str = Form(""),
    category: str = Form(...),
    file: UploadFile = File(...),
    csrf: str = Form(""),
):
    check_csrf(request, csrf)
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", 303)
    if not user.is_admin:
        raise HTTPException(403, "Only the admin can upload")

    title, description = title.strip(), description.strip()
    original = Path(file.filename or "").name
    ext = Path(original).suffix.lower()
    error = None
    if not (3 <= len(title) <= 120):
        error = "Title must be 3-120 characters."
    elif len(description) > 2000:
        error = "Description is too long (max 2000)."
    elif category not in config.CATEGORIES:
        error = "Pick a valid category."
    elif ext not in config.ALLOWED_EXT:
        error = f"Allowed file types: {', '.join(sorted(config.ALLOWED_EXT))}"
    if error:
        return render(request, db, "upload.html", status=400, error=error,
                      title_val=title, description=description, category_val=category)

    stored = f"{uuid.uuid4().hex}{ext}"
    path = config.UPLOAD_DIR / stored
    size = 0
    try:
        with open(path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > config.MAX_UPLOAD_BYTES:
                    raise ValueError("too big")
                out.write(chunk)
        if size == 0:
            raise ValueError("empty")
    except ValueError as e:
        path.unlink(missing_ok=True)
        msg = f"File too large (max {config.MAX_UPLOAD_BYTES // (1024*1024)} MB)." if str(e) == "too big" else "File is empty."
        return render(request, db, "upload.html", status=400, error=msg,
                      title_val=title, description=description, category_val=category)

    db.add(Cheatsheet(title=title, description=description, category=category,
                      stored_name=stored, original_name=original, size=size, owner_id=user.id,
                      status="approved" if user.is_admin else "pending"))
    db.commit()
    flash(request, "Published." if user.is_admin else "Uploaded! It will appear publicly after admin approval.")
    return RedirectResponse("/mine", 303)


@app.get("/sheet/{sheet_id}")
def sheet_detail(sheet_id: int, request: Request, db: Session = Depends(get_db)):
    sheet = db.scalar(
        select(Cheatsheet).options(joinedload(Cheatsheet.owner)).where(Cheatsheet.id == sheet_id)
    )
    user = current_user(request, db)
    if not sheet or not (sheet.status == "approved" or (user and (user.is_admin or user.id == sheet.owner_id))):
        raise HTTPException(404, "Not found")
    return render(request, db, "sheet.html", sheet=sheet)


@app.get("/download/{sheet_id}")
def download(sheet_id: int, request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse(f"/login?next=/download/{sheet_id}", 303)
    rate_limit(f"dl:{user.id}", 60, 3600)
    sheet = db.get(Cheatsheet, sheet_id)
    path = config.UPLOAD_DIR / sheet.stored_name if sheet else None
    allowed = sheet and (sheet.status == "approved" or (user and (user.is_admin or user.id == sheet.owner_id)))
    if not allowed or not path.exists():
        raise HTTPException(404, "File not found")
    if sheet.status == "approved":
        db.execute(update(Cheatsheet).where(Cheatsheet.id == sheet_id).values(downloads=Cheatsheet.downloads + 1))
        db.commit()
    return FileResponse(path, filename=sheet.original_name)  # sent as attachment


@app.post("/delete/{sheet_id}")
def delete(sheet_id: int, request: Request, db: Session = Depends(get_db), csrf: str = Form("")):
    check_csrf(request, csrf)
    user = current_user(request, db)
    sheet = db.get(Cheatsheet, sheet_id)
    if not user or not sheet or (sheet.owner_id != user.id and not user.is_admin):
        raise HTTPException(403, "Not allowed")
    back = "/mine" if sheet.owner_id == user.id else "/admin"
    (config.UPLOAD_DIR / sheet.stored_name).unlink(missing_ok=True)
    db.delete(sheet)
    db.commit()
    flash(request, "Deleted.")
    return RedirectResponse(back, 303)


# ---------- admin ----------
def require_admin(request: Request, db: Session) -> User:
    user = current_user(request, db)
    if not user or not user.is_admin:
        raise HTTPException(403, "Admins only")
    return user


@app.get("/admin")
def admin_panel(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    base = select(Cheatsheet).options(joinedload(Cheatsheet.owner))
    pending = db.scalars(base.where(Cheatsheet.status == "pending").order_by(Cheatsheet.created_at)).all()
    reviewed = db.scalars(
        base.where(Cheatsheet.status != "pending").order_by(Cheatsheet.created_at.desc()).limit(30)
    ).all()
    return render(request, db, "admin.html", pending=pending, reviewed=reviewed)


@app.post("/admin/review/{sheet_id}")
def review(sheet_id: int, request: Request, db: Session = Depends(get_db),
           action: str = Form(...), csrf: str = Form("")):
    check_csrf(request, csrf)
    require_admin(request, db)
    sheet = db.get(Cheatsheet, sheet_id)
    if not sheet:
        raise HTTPException(404)
    if action not in ("approve", "reject"):
        raise HTTPException(400, "Bad action")
    sheet.status = "approved" if action == "approve" else "rejected"
    db.commit()
    flash(request, f"{sheet.title}: {sheet.status}.")
    return RedirectResponse("/admin", 303)


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"
