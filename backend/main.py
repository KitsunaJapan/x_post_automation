import os
import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

import anthropic
from database import db, ScheduledPost
from twitter_client import post_tweet
from auth import (
    hash_password, verify_password,
    create_access_token,
    get_current_user, require_admin,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Twitter Auto Poster")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

jobstores = {
    "default": SQLAlchemyJobStore(url=os.getenv("DATABASE_URL", "sqlite:///./scheduler.db"))
}
scheduler = BackgroundScheduler(jobstores=jobstores, timezone="Asia/Tokyo")
scheduler.start()

# ── Bootstrap admin on first boot ────────────────────────────────────────────

def _bootstrap_admin():
    admin_user = os.getenv("ADMIN_USERNAME", "admin")
    admin_pass = os.getenv("ADMIN_PASSWORD")
    if not admin_pass:
        logger.warning("ADMIN_PASSWORD not set — skipping admin bootstrap")
        return
    if not db.user_exists(admin_user):
        db.create_user(
            username=admin_user,
            display_name="管理者",
            password_hash=hash_password(admin_pass),
            role="admin",
        )
        logger.info(f"Bootstrap: admin user '{admin_user}' created")

_bootstrap_admin()


# ── Models ───────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

class CreateUserRequest(BaseModel):
    username: str
    display_name: str
    password: str
    role: str = "member"

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class PostItem(BaseModel):
    text: str
    scheduled_at: datetime
    image_paths: list[str] = []

class ScheduleRequest(BaseModel):
    account_id: str
    posts: list[PostItem]

class AccountConfig(BaseModel):
    account_id: str
    label: str
    api_key: str
    api_secret: str
    access_token: str
    access_token_secret: str


# ── Auth routes (public) ──────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.post("/api/auth/login")
def login(req: LoginRequest):
    user = db.get_user(req.username)
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="ユーザー名またはパスワードが正しくありません")
    db.touch_last_login(req.username)
    token = create_access_token(username=user["username"], role=user["role"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
    }


@app.get("/api/auth/me")
def me(user: dict = Depends(get_current_user)):
    record = db.get_user(user["sub"])
    if not record:
        raise HTTPException(status_code=404, detail="User not found")
    return {"username": record["username"], "display_name": record["display_name"], "role": record["role"]}


@app.post("/api/auth/change-password")
def change_password(req: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    record = db.get_user(user["sub"])
    if not record or not verify_password(req.current_password, record["password_hash"]):
        raise HTTPException(status_code=400, detail="現在のパスワードが正しくありません")
    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="パスワードは8文字以上にしてください")
    db.update_user_password(user["sub"], hash_password(req.new_password))
    return {"ok": True}


# ── User management (admin only) ──────────────────────────────────────────────

@app.get("/api/users")
def list_users(admin: dict = Depends(require_admin)):
    return db.get_all_users()


@app.post("/api/users")
def create_user(req: CreateUserRequest, admin: dict = Depends(require_admin)):
    if db.user_exists(req.username):
        raise HTTPException(status_code=409, detail="そのユーザー名はすでに使われています")
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="パスワードは8文字以上にしてください")
    if req.role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="role は admin または member のみ")
    db.create_user(req.username, req.display_name, hash_password(req.password), req.role)
    return {"ok": True, "username": req.username}


@app.patch("/api/users/{username}/role")
def update_role(username: str, body: dict, admin: dict = Depends(require_admin)):
    role = body.get("role")
    if role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="Invalid role")
    db.update_user_role(username, role)
    return {"ok": True}


@app.patch("/api/users/{username}/active")
def set_active(username: str, body: dict, admin: dict = Depends(require_admin)):
    if username == admin["sub"]:
        raise HTTPException(status_code=400, detail="自分自身は無効化できません")
    db.set_user_active(username, bool(body.get("is_active", True)))
    return {"ok": True}


@app.delete("/api/users/{username}")
def delete_user(username: str, admin: dict = Depends(require_admin)):
    if username == admin["sub"]:
        raise HTTPException(status_code=400, detail="自分自身は削除できません")
    db.set_user_active(username, False)
    return {"ok": True}


# ── Twitter account routes (login required) ───────────────────────────────────

@app.get("/api/accounts")
def list_accounts(user: dict = Depends(get_current_user)):
    accounts = db.get_accounts()
    return [{"account_id": a["account_id"], "label": a["label"]} for a in accounts]


@app.post("/api/accounts")
def save_account(cfg: AccountConfig, admin: dict = Depends(require_admin)):
    db.upsert_account(cfg.dict())
    return {"ok": True, "account_id": cfg.account_id}


@app.delete("/api/accounts/{account_id}")
def delete_account(account_id: str, admin: dict = Depends(require_admin)):
    db.delete_account(account_id)
    return {"ok": True}


# ── Post routes (login required) ──────────────────────────────────────────────

@app.post("/api/upload-image")
async def upload_image(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    upload_dir = "uploads"
    os.makedirs(upload_dir, exist_ok=True)
    ext = file.filename.rsplit(".", 1)[-1] if "." in file.filename else "jpg"
    filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(upload_dir, filename)
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)
    return {"path": filepath, "filename": filename}


@app.post("/api/schedule")
def schedule_posts(req: ScheduleRequest, user: dict = Depends(get_current_user)):
    account = db.get_account(req.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    created = []
    for item in req.posts:
        post_id = uuid.uuid4().hex
        post_data = ScheduledPost(
            post_id=post_id,
            account_id=req.account_id,
            text=item.text,
            scheduled_at=item.scheduled_at,
            image_paths=json.dumps(item.image_paths),
            status="pending",
        )
        db.save_post(post_data)
        run_time = item.scheduled_at
        if run_time.tzinfo is None:
            run_time = run_time.replace(tzinfo=timezone.utc)
        scheduler.add_job(
            execute_post, trigger="date", run_date=run_time,
            args=[post_id], id=post_id, replace_existing=True,
        )
        created.append({"post_id": post_id, "scheduled_at": run_time.isoformat()})
        logger.info(f"[{user['sub']}] Scheduled post {post_id} at {run_time}")
    return {"ok": True, "scheduled": created}


@app.get("/api/posts")
def list_posts(status: Optional[str] = None, user: dict = Depends(get_current_user)):
    return db.get_posts(status=status)


@app.delete("/api/posts/{post_id}")
def cancel_post(post_id: str, user: dict = Depends(get_current_user)):
    try:
        scheduler.remove_job(post_id)
    except Exception:
        pass
    db.update_post_status(post_id, "cancelled")
    return {"ok": True}


# ── Job executor ──────────────────────────────────────────────────────────────

def execute_post(post_id: str):
    post = db.get_post(post_id)
    if not post or post["status"] != "pending":
        return
    account = db.get_account(post["account_id"])
    if not account:
        logger.error(f"Account not found for post {post_id}")
        db.update_post_status(post_id, "failed", error="Account not found")
        return
    try:
        image_paths = json.loads(post.get("image_paths", "[]"))
        tweet_id = post_tweet(
            api_key=account["api_key"],
            api_secret=account["api_secret"],
            access_token=account["access_token"],
            access_token_secret=account["access_token_secret"],
            text=post["text"],
            image_paths=image_paths,
        )
        db.update_post_status(post_id, "posted", tweet_id=tweet_id)
        logger.info(f"Posted tweet {tweet_id} for post {post_id}")
    except Exception as e:
        logger.error(f"Failed to post {post_id}: {e}")
        db.update_post_status(post_id, "failed", error=str(e))


# ── Static frontend ───────────────────────────────────────────────────────────
if os.path.exists("frontend/public"):
    app.mount("/", StaticFiles(directory="frontend/public", html=True), name="frontend")


# ── AI generation route (login required) ─────────────────────────────────────

class GenerateRequest(BaseModel):
    subject: str
    purpose: str = ""
    count: int = 1
    existing: list[str] = []
    single_index: Optional[int] = None

@app.post("/api/generate")
def generate_tweets(req: GenerateRequest, user: dict = Depends(get_current_user)):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY が設定されていません")

    client = anthropic.Anthropic(api_key=api_key)

    if req.single_index is not None:
        existing_text = "\n".join(req.existing) if req.existing else ""
        prompt = (
            f"Twitterの投稿1件を作成してください。\n"
            f"主題：{req.subject}\n"
            f"目的：{req.purpose or '情報発信'}\n"
            + (f"他の投稿との重複を避けてください:\n{existing_text}\n" if existing_text else "")
            + "条件：280文字以内の日本語、ハッシュタグ1〜3個、投稿文のみ出力"
        )
        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        return {"posts": [text]}
    else:
        prompt = (
            f"Twitterの投稿を{req.count}件作成してください。\n"
            f"主題：{req.subject}\n"
            f"目的：{req.purpose or '情報発信'}\n"
            f"条件：各280文字以内の日本語、ハッシュタグ1〜3個、各投稿を「---」で区切る、投稿文のみ出力"
        )
        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text
        posts = [p.strip() for p in text.split("---") if p.strip()]
        return {"posts": posts[:req.count]}
