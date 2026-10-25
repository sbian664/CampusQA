"""WeChat identity and revocable, hash-only user credentials."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import hashlib
import os
import secrets
import sqlite3
import time
import uuid

import requests
from fastapi import HTTPException


current_user = ContextVar('campusqa_user', default=None)


def require_user_id():
    user_id = current_user.get()
    if not user_id:
        raise HTTPException(status_code=401, detail='请先完成微信登录')
    return user_id


class UserStore:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS app_users (
                    user_id TEXT PRIMARY KEY, app_id TEXT NOT NULL,
                    open_id TEXT NOT NULL, created_at REAL NOT NULL,
                    UNIQUE(app_id, open_id));
                CREATE TABLE IF NOT EXISTS user_tokens (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    expires_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_user_tokens_expiry ON user_tokens(expires_at);
            ''')

    @contextmanager
    def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def hash_token(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def login(self, app_id, open_id):
        now = time.time()
        token = secrets.token_urlsafe(32)
        expires = now + 7 * 86400
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO app_users VALUES (?, ?, ?, ?)',
                       (uuid.uuid4().hex, app_id, open_id, now))
            user_id = db.execute('SELECT user_id FROM app_users WHERE app_id=? AND open_id=?', (app_id, open_id)).fetchone()[0]
            db.execute('DELETE FROM user_tokens WHERE expires_at <= ?', (now,))
            db.execute('INSERT INTO user_tokens VALUES (?, ?, ?)', (self.hash_token(token), user_id, expires))
        return {'user_id': user_id, 'token': token, 'token_type': 'Bearer', 'expires_at': expires}

    def authenticate(self, token):
        if not token or len(token) > 512:
            return None
        with self.connect() as db:
            row = db.execute('SELECT user_id FROM user_tokens WHERE token_hash=? AND expires_at>?', (self.hash_token(token), time.time())).fetchone()
        return row['user_id'] if row else None

    def revoke(self, token):
        with self.connect() as db:
            db.execute('DELETE FROM user_tokens WHERE token_hash=?', (self.hash_token(token),))


def exchange_wechat_code(code):
    app_id = os.getenv('WECHAT_APP_ID', '').strip()
    secret = os.getenv('WECHAT_APP_SECRET', '').strip()
    if not app_id or not secret:
        raise HTTPException(status_code=503, detail='微信登录尚未配置')
    try:
        response = requests.get('https://api.weixin.qq.com/sns/jscode2session',
                                params={'appid': app_id, 'secret': secret, 'js_code': code, 'grant_type': 'authorization_code'},
                                timeout=(5, 10), allow_redirects=False)
        if response.status_code != 200:
            raise ValueError('upstream status')
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError('invalid upstream response')
    except (requests.RequestException, ValueError):
        raise HTTPException(status_code=502, detail='微信登录服务暂不可用，请重试') from None
    if result.get('errcode') or not isinstance(result.get('openid'), str) or not result['openid']:
        raise HTTPException(status_code=401, detail='微信登录验证失败，请重试')
    return app_id, result['openid']
