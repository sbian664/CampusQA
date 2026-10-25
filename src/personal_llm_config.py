"""Per-user model selection; keys never cross users or endpoint boundaries."""
import os

from cryptography.fernet import Fernet, InvalidToken
from src.user_llm_config import normalize_llm_config


class PersonalLLMConfigStore:
    def __init__(self, users, encryption_key=None):
        self.users = users
        self.encryption_key = encryption_key
        with users.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS user_llm_configs (
                user_id TEXT PRIMARY KEY, source TEXT NOT NULL DEFAULT 'server',
                provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
                base_url TEXT NOT NULL DEFAULT '', encrypted_api_key TEXT NOT NULL DEFAULT '')''')

    def _cipher(self):
        key = self.encryption_key or os.getenv('USER_LLM_ENCRYPTION_KEY', '')
        try:
            return Fernet(key)
        except (ValueError, TypeError):
            raise ValueError('个人模型加密密钥未配置或无效，请联系管理员') from None

    def _row(self, user_id):
        with self.users.connect() as db:
            row = db.execute('SELECT * FROM user_llm_configs WHERE user_id=?', (user_id,)).fetchone()
        return dict(row) if row else None

    def _decrypt(self, row):
        try:
            return self._cipher().decrypt(row['encrypted_api_key'].encode()).decode()
        except (InvalidToken, UnicodeError):
            raise ValueError('个人模型密钥无法解密，请重新填写或联系管理员') from None

    def public_config(self, user_id):
        row = self._row(user_id) or {}
        return {key: row.get(key, '') for key in ('provider', 'model', 'base_url')} | {
            'source': row.get('source', 'server'), 'has_api_key': bool(row.get('encrypted_api_key'))}

    def resolve(self, user_id, payload):
        incoming = dict(payload)
        incoming['provider'] = str(incoming.get('provider', '')).strip().lower()
        row = self._row(user_id)
        if incoming.get('provider') == 'local':
            raise ValueError('个人配置不支持 local 供应商')
        if not str(incoming.get('api_key', '')).strip():
            if (row and incoming.get('provider') == row['provider']
                    and str(incoming.get('base_url', '')).strip().rstrip('/') == row['base_url']
                    and row['encrypted_api_key']):
                incoming['api_key'] = self._decrypt(row)
        return normalize_llm_config(incoming)

    def save(self, user_id, payload):
        source = payload.get('source', 'personal')
        if source not in {'server', 'personal'}:
            raise ValueError('模型来源必须为 server 或 personal')
        # Switching to server keeps the personal configuration until explicitly deleted.
        if source == 'server':
            with self.users.connect() as db:
                db.execute("INSERT INTO user_llm_configs(user_id, source) VALUES (?, 'server') ON CONFLICT(user_id) DO UPDATE SET source='server'", (user_id,))
            return self.public_config(user_id)
        config = self.resolve(user_id, payload)
        encrypted = self._cipher().encrypt(config['api_key'].encode()).decode()
        with self.users.connect() as db:
            db.execute('''INSERT INTO user_llm_configs VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET source=excluded.source, provider=excluded.provider,
                model=excluded.model, base_url=excluded.base_url, encrypted_api_key=excluded.encrypted_api_key''',
                       (user_id, source, config['provider'], config['model'], config['base_url'], encrypted))
        return self.public_config(user_id)

    def delete(self, user_id):
        with self.users.connect() as db:
            db.execute('DELETE FROM user_llm_configs WHERE user_id=?', (user_id,))
        return self.public_config(user_id)

    def effective(self, user_id, server_config):
        row = self._row(user_id)
        if not row or row['source'] == 'server':
            return dict(server_config)
        return normalize_llm_config({key: row[key] for key in ('provider', 'model', 'base_url')} | {'api_key': self._decrypt(row)})
