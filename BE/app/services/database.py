import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import psycopg
from psycopg.rows import dict_row

from app.domain.credential import BadgeEvaluation
from app.services.key_custody import StoredHolderKey


def get_db_connection():
    return psycopg.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "password"),
        dbname=os.getenv("DB_NAME", "vc_career_agent"),
        row_factory=dict_row,
        autocommit=True,
    )


class EmailAlreadyRegistered(Exception):
    pass


class DatabaseService:
    # ── 사용자 ────────────────────────────────────────────────
    def create_user(self, *, email: str, name: str, did: str, password_hash: str,
                    user_id: Optional[str] = None, holder_key: Optional[StoredHolderKey] = None) -> dict:
        """사용자와 (있으면) 관리형 홀더 키를 한 트랜잭션으로 저장한다."""
        now = datetime.now(timezone.utc)
        user_id = user_id or str(uuid.uuid4())
        try:
            with get_db_connection() as conn:
                with conn.transaction(), conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO users (id, email, name, subject_did, password_hash, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING *
                        """,
                        (user_id, email, name, did, password_hash, now, now),
                    )
                    user = cur.fetchone()
                    if holder_key is not None:
                        cur.execute(
                            """
                            INSERT INTO holder_keys (user_id, did, key_type, public_key_multibase,
                                                     encrypted_private_key, nonce, kek_id, created_at)
                            VALUES (%s, %s, 'Ed25519', %s, %s, %s, %s, %s)
                            """,
                            (user_id, holder_key.did, holder_key.public_key_multibase,
                             holder_key.encrypted_private_key, holder_key.nonce, holder_key.kek_id, now),
                        )
                    return user
        except psycopg.errors.UniqueViolation as exc:
            raise EmailAlreadyRegistered(email) from exc

    def get_holder_key(self, user_id: str) -> Optional[StoredHolderKey]:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM holder_keys WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
        if not row:
            return None
        return StoredHolderKey(
            did=row["did"],
            public_key_multibase=row["public_key_multibase"],
            encrypted_private_key=bytes(row["encrypted_private_key"]),
            nonce=bytes(row["nonce"]),
            kek_id=row["kek_id"],
        )

    def get_user_by_email(self, email: str) -> Optional[dict]:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE lower(email) = lower(%s)", (email,))
            return cur.fetchone()

    def get_user_by_id(self, user_id: str) -> Optional[dict]:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            return cur.fetchone()

    def get_user_by_did(self, did: str) -> Optional[dict]:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE subject_did = %s", (did,))
            return cur.fetchone()

    # ── 자격증명 ──────────────────────────────────────────────
    def save_badge(self, *, user_id: str, provider: str, evaluation: BadgeEvaluation, vc_jwt: str) -> dict:
        """검증 결과를 credentials(원본 vcJwt 포함)와 verification_checks에 한 트랜잭션으로 저장한다."""
        now = datetime.now(timezone.utc)
        with get_db_connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO credentials (
                        record_id, user_id, source_provider, source_external_id, format,
                        achievement_name, issuer_name, issuer_trust_level, status,
                        issued_at, expires_at, raw_data, created_at, raw_token, last_verified_at
                    )
                    VALUES (%s, %s, %s, %s, 'OB3.0', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, source_provider, source_external_id)
                    DO UPDATE SET
                        achievement_name = EXCLUDED.achievement_name,
                        issuer_name = EXCLUDED.issuer_name,
                        issuer_trust_level = EXCLUDED.issuer_trust_level,
                        status = EXCLUDED.status,
                        expires_at = EXCLUDED.expires_at,
                        raw_data = EXCLUDED.raw_data,
                        raw_token = EXCLUDED.raw_token,
                        last_verified_at = EXCLUDED.last_verified_at
                    RETURNING record_id, status
                    """,
                    (
                        evaluation.record_id, user_id, provider, evaluation.external_id,
                        evaluation.achievement_name, evaluation.issuer_name, evaluation.issuer_trust_level,
                        evaluation.status, evaluation.issued_at, evaluation.expires_at,
                        json.dumps(evaluation.vc), now, vc_jwt, now,
                    ),
                )
                saved = cur.fetchone()
                cur.execute("DELETE FROM verification_checks WHERE record_id = %s", (saved["record_id"],))
                cur.executemany(
                    """
                    INSERT INTO verification_checks (record_id, check_name, result, detail, checked_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    [(saved["record_id"], c.name, c.result, c.detail, now) for c in evaluation.checks],
                )
        return {"record_id": str(saved["record_id"]), "status": saved["status"]}

    def find_credential_owner(self, record_id: str) -> Optional[str]:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT user_id FROM credentials WHERE record_id = %s", (record_id,))
            row = cur.fetchone()
            return str(row["user_id"]) if row else None

    def get_user_and_credentials(self, user_did: str):
        """FE 대시보드/AI가 쓰는 careers 응답. 응답 형식은 FE `CareersResponse` 계약이므로 바꾸지 않는다."""
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE subject_did = %s", (user_did,))
            user = cur.fetchone()
            if not user:
                return None

            cur.execute("SELECT * FROM credentials WHERE user_id = %s ORDER BY issued_at DESC", (user["id"],))
            credentials = cur.fetchall()

        return {
            "user_profile": {
                "id": str(user["id"]),
                "did": user["subject_did"],
                "email": user["email"],
                "name": user["name"],
            },
            "verified_credentials": [
                {
                    "record_id": str(c["record_id"]),
                    "issuer": c["issuer_name"],
                    "achievement_name": c["achievement_name"],
                    "status": c["status"],
                    "issued_at": c["issued_at"].isoformat() if isinstance(c["issued_at"], datetime) else str(c["issued_at"]),
                    "raw_data": c["raw_data"],
                }
                for c in credentials
            ],
        }


db_service = DatabaseService()
