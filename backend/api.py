import os
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54394/printreg")
SECRET = os.environ.get("JWT_SECRET", "print-register-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)
USERS = {
    "printer": {"role": "writer", "password_hash": pwd.hash("print123456")},
    "checker": {"role": "reader", "password_hash": pwd.hash("check123456")},
}


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id serial PRIMARY KEY,
    sheet text NOT NULL,
    machine text NOT NULL DEFAULT '',
    cyan_mm double precision NOT NULL,
    magenta_mm double precision NOT NULL,
    status text NOT NULL,
    verdict text NOT NULL DEFAULT '',
    reason text NOT NULL DEFAULT '',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS handovers (
    id serial PRIMARY KEY,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS handover_items (
    id serial PRIMARY KEY,
    handover_id integer NOT NULL REFERENCES handovers(id),
    job_id integer NOT NULL,
    sheet text NOT NULL,
    machine text NOT NULL,
    status text NOT NULL
);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS machine text NOT NULL DEFAULT '';
"""


class LoginIn(BaseModel):
    username: str
    password: str


class JobIn(BaseModel):
    sheet: str
    machine: str = ""
    cyan_mm: float
    magenta_mm: float


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = jwt.decode(credentials.credentials, SECRET, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="无效令牌") from exc
    if payload.get("sub") not in USERS:
        raise HTTPException(status_code=401, detail="无效令牌")
    return {"username": payload["sub"], "role": payload.get("role")}


def require_writer(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail="仅印刷员可操作")
    return user


app = FastAPI(title="印刷套准复核台")


@app.on_event("startup")
def startup():
    with connect() as conn:
        conn.execute(SCHEMA)
        n = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        if n == 0:
            now = datetime.now(timezone.utc)
            conn.execute(
                """INSERT INTO jobs (sheet, machine, cyan_mm, magenta_mm, status, verdict, reason, created_by, created_at)
                   VALUES
                   ('封面-01', '一号机', 0.05, -0.04, 'pending', '', '', 'printer', %s),
                   ('内页-09', '二号机', 0.40, 0.02, 'pending', '', '', 'printer', %s)""",
                (now, now),
            )
        conn.commit()


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "print-register-review"}


@app.post("/api/auth/login")
def login(body: LoginIn):
    user = USERS.get(body.username.strip())
    if not user or not pwd.verify(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode({"sub": body.username.strip(), "role": user["role"], "exp": exp}, SECRET, algorithm="HS256")
    return {"access_token": token, "username": body.username.strip(), "role": user["role"]}


@app.get("/api/jobs")
def list_jobs(_user: dict = Depends(current_user)):
    with connect() as conn:
        return conn.execute(
            "SELECT id, sheet, machine, cyan_mm, magenta_mm, status, verdict, reason, created_by FROM jobs ORDER BY id DESC"
        ).fetchall()


@app.post("/api/jobs", status_code=202)
def enqueue(body: JobIn, user: dict = Depends(require_writer)):
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO jobs (sheet, machine, cyan_mm, magenta_mm, status, created_by, created_at)
               VALUES (%s, %s, %s, %s, 'pending', %s, %s)
               RETURNING id, sheet, machine, status, verdict""",
            (body.sheet.strip(), body.machine.strip(), body.cyan_mm, body.magenta_mm, user["username"], datetime.now(timezone.utc)),
        ).fetchone()
        conn.commit()
    return row


@app.post("/api/handovers", status_code=201)
def create_handover(user: dict = Depends(require_writer)):
    """一键交班：把当时所有待处理与领取中的印张抄进只读快照。"""
    with connect() as conn:
        head = conn.execute(
            "INSERT INTO handovers (created_by, created_at) VALUES (%s, %s) RETURNING id, created_by, created_at",
            (user["username"], datetime.now(timezone.utc)),
        ).fetchone()
        items = conn.execute(
            """INSERT INTO handover_items (handover_id, job_id, sheet, machine, status)
               SELECT %s, id, sheet, machine, status FROM jobs
               WHERE status IN ('pending', 'running')
               ORDER BY id
               RETURNING job_id, sheet, machine, status""",
            (head["id"],),
        ).fetchall()
        conn.commit()
    return {**head, "items": items}


@app.get("/api/handovers")
def list_handovers(_user: dict = Depends(current_user)):
    with connect() as conn:
        return conn.execute(
            """SELECT h.id, h.created_by, h.created_at,
                      (SELECT COUNT(*) FROM handover_items i WHERE i.handover_id = h.id) AS item_count
               FROM handovers h
               ORDER BY h.id DESC"""
        ).fetchall()


@app.get("/api/handovers/{handover_id}")
def handover_detail(handover_id: int, _user: dict = Depends(current_user)):
    with connect() as conn:
        head = conn.execute(
            "SELECT id, created_by, created_at FROM handovers WHERE id = %s",
            (handover_id,),
        ).fetchone()
        if head is None:
            raise HTTPException(status_code=404, detail="快照不存在")
        items = conn.execute(
            """SELECT job_id, sheet, machine, status FROM handover_items
               WHERE handover_id = %s ORDER BY job_id""",
            (handover_id,),
        ).fetchall()
    return {**head, "items": items}
