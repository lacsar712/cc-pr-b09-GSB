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

# 待处理(pending)与领取中(running)是交班时需要冻结的状态
OPEN_STATUSES = ("pending", "running")


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id serial PRIMARY KEY,
    sheet text NOT NULL,
    press text NOT NULL DEFAULT '',
    cyan_mm double precision NOT NULL,
    magenta_mm double precision NOT NULL,
    status text NOT NULL,
    verdict text NOT NULL DEFAULT '',
    reason text NOT NULL DEFAULT '',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS press text NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS handover_snapshots (
    id serial PRIMARY KEY,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    item_count integer NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS handover_items (
    id serial PRIMARY KEY,
    snapshot_id integer NOT NULL REFERENCES handover_snapshots(id),
    job_id integer NOT NULL,
    sheet text NOT NULL,
    press text NOT NULL DEFAULT '',
    cyan_mm double precision NOT NULL,
    magenta_mm double precision NOT NULL,
    status text NOT NULL,
    created_by text NOT NULL
);
"""


class LoginIn(BaseModel):
    username: str
    password: str


class JobIn(BaseModel):
    sheet: str
    press: str = ""
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
        raise HTTPException(status_code=403, detail="仅印刷员可交班")
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
                """INSERT INTO jobs (sheet, press, cyan_mm, magenta_mm, status, verdict, reason, created_by, created_at)
                   VALUES
                   ('封面-01', '1号机', 0.05, -0.04, 'pending', '', '', 'printer', %s),
                   ('内页-09', '2号机', 0.40, 0.02, 'pending', '', '', 'printer', %s)""",
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
            "SELECT id, sheet, press, cyan_mm, magenta_mm, status, verdict, reason, created_by FROM jobs ORDER BY id DESC"
        ).fetchall()


@app.post("/api/jobs", status_code=202)
def enqueue(body: JobIn, user: dict = Depends(require_writer)):
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO jobs (sheet, press, cyan_mm, magenta_mm, status, created_by, created_at)
               VALUES (%s, %s, %s, %s, 'pending', %s, %s)
               RETURNING id, sheet, press, status, verdict""",
            (
                body.sheet.strip(),
                body.press.strip(),
                body.cyan_mm,
                body.magenta_mm,
                user["username"],
                datetime.now(timezone.utc),
            ),
        ).fetchone()
        conn.commit()
    return row


@app.post("/api/handovers", status_code=201)
def create_handover(user: dict = Depends(require_writer)):
    """点交班:把当时所有待处理与领取中的印张复制成只读快照。"""
    with connect() as conn:
        # 同一事务内锁定开放中的作业,保证快照与交班当时一致
        open_items = conn.execute(
            """SELECT id, sheet, press, cyan_mm, magenta_mm, status, created_by
               FROM jobs
               WHERE status = ANY(%s)
               ORDER BY id
               FOR SHARE""",
            (list(OPEN_STATUSES),),
        ).fetchall()
        snapshot = conn.execute(
            "INSERT INTO handover_snapshots (created_by, created_at, item_count) VALUES (%s, %s, %s) RETURNING id",
            (user["username"], datetime.now(timezone.utc), len(open_items)),
        ).fetchone()
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO handover_items
                       (snapshot_id, job_id, sheet, press, cyan_mm, magenta_mm, status, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                [
                    (
                        snapshot["id"],
                        item["id"],
                        item["sheet"],
                        item["press"],
                        item["cyan_mm"],
                        item["magenta_mm"],
                        item["status"],
                        item["created_by"],
                    )
                    for item in open_items
                ],
            )
        conn.commit()
        snapshot_id = snapshot["id"]
    return {"id": snapshot_id, "item_count": len(open_items)}


@app.get("/api/handovers")
def list_handovers(_user: dict = Depends(current_user)):
    """交班历史快照列表(只读账号也可翻阅)。"""
    with connect() as conn:
        return conn.execute(
            """SELECT id, created_by, created_at, item_count
               FROM handover_snapshots
               ORDER BY id DESC"""
        ).fetchall()


@app.get("/api/handovers/{snapshot_id}")
def get_handover(snapshot_id: int, _user: dict = Depends(current_user)):
    """单份交班快照明细,永远呈现交班当时冻结的内容。"""
    with connect() as conn:
        snapshot = conn.execute(
            "SELECT id, created_by, created_at, item_count FROM handover_snapshots WHERE id = %s",
            (snapshot_id,),
        ).fetchone()
        if snapshot is None:
            raise HTTPException(status_code=404, detail="快照不存在")
        items = conn.execute(
            """SELECT job_id, sheet, press, cyan_mm, magenta_mm, status, created_by
               FROM handover_items
               WHERE snapshot_id = %s
               ORDER BY id""",
            (snapshot_id,),
        ).fetchall()
    return {**snapshot, "items": items}
