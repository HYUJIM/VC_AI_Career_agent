import os
import json
import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime

class UserCreate(BaseModel):
    id: str
    email: str
    name: str
    subject_did: str
    created_at: str
    updated_at: str

class CredentialCreate(BaseModel):
    record_id: str
    user_id: str
    source_provider: str
    source_external_id: str
    format: str
    achievement_name: str
    issuer_name: str
    issuer_trust_level: str
    status: str
    issued_at: str
    expires_at: Optional[str] = None
    awarded_date: Optional[str] = None
    raw_data: Dict[Any, Any]
    created_at: str

def get_db_connection():
    return psycopg.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "password"),
        dbname=os.getenv("DB_NAME", "vc_career_agent"),
        row_factory=dict_row,
        autocommit=True
    )

class DatabaseService:
    def save_user(self, user: UserCreate):
        query = """
            INSERT INTO users (id, email, name, subject_did, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (subject_did) 
            DO UPDATE SET 
                email = EXCLUDED.email, 
                name = EXCLUDED.name, 
                updated_at = NOW()
            RETURNING *;
        """
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (
                    user.id, user.email, user.name, user.subject_did, user.created_at, user.updated_at
                ))
                return cur.fetchone()

    def save_credential(self, cred: CredentialCreate):
        user_id = cred.user_id
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # did로 넘어온 경우 UUID로 변환
                if user_id.startswith("did:"):
                    cur.execute("SELECT id FROM users WHERE subject_did = %s", (user_id,))
                    row = cur.fetchone()
                    if row:
                        user_id = row['id']
                    else:
                        # 테스트 편의성을 위해 유저가 없으면 임시 유저를 자동 생성합니다.
                        import uuid
                        from datetime import datetime, timezone
                        new_uuid = str(uuid.uuid4())
                        now_str = datetime.now(timezone.utc).isoformat()
                        cur.execute(
                            "INSERT INTO users (id, email, name, subject_did, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s)",
                            (new_uuid, "test@example.com", "테스트 유저", user_id, now_str, now_str)
                        )
                        user_id = new_uuid

                query = """
                    INSERT INTO credentials (
                        record_id, user_id, source_provider, source_external_id, format, 
                        achievement_name, issuer_name, issuer_trust_level, status, 
                        issued_at, expires_at, awarded_date, raw_data, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, source_provider, source_external_id) 
                    DO UPDATE SET 
                        status = EXCLUDED.status,
                        issuer_trust_level = EXCLUDED.issuer_trust_level,
                        raw_data = EXCLUDED.raw_data
                    RETURNING *;
                """
                cur.execute(query, (
                    cred.record_id, user_id, cred.source_provider, cred.source_external_id, 
                    cred.format, cred.achievement_name, cred.issuer_name, cred.issuer_trust_level, 
                    cred.status, cred.issued_at, cred.expires_at, cred.awarded_date, 
                    json.dumps(cred.raw_data), cred.created_at
                ))
                return cur.fetchone()

    def get_user_and_credentials(self, user_did: str):
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE subject_did = %s", (user_did,))
                user = cur.fetchone()
                if not user:
                    return None
                
                cur.execute("SELECT * FROM credentials WHERE user_id = %s ORDER BY issued_at DESC", (user['id'],))
                credentials = cur.fetchall()

                return {
                    "user_profile": {
                        "id": str(user['id']),
                        "did": user['subject_did'],
                        "email": user['email'],
                        "name": user['name']
                    },
                    "verified_credentials": [
                        {
                            "record_id": str(c['record_id']),
                            "issuer": c['issuer_name'],
                            "achievement_name": c['achievement_name'],
                            "status": c['status'],
                            "issued_at": c['issued_at'].isoformat() if isinstance(c['issued_at'], datetime) else str(c['issued_at']),
                            "raw_data": c['raw_data']
                        } for c in credentials
                    ]
                }

db_service = DatabaseService()
