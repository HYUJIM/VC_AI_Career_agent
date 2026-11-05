import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import psycopg
from psycopg.rows import dict_row

from app.domain.credential import BadgeEvaluation


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
    def create_user(self, *, email: str, name: str, did: str, password_hash: str) -> dict:
        now = datetime.now(timezone.utc)
        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (id, email, name, subject_did, password_hash, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (str(uuid.uuid4()), email, name, did, password_hash, now, now),
                )
                return cur.fetchone()
        except psycopg.errors.UniqueViolation as exc:
            raise EmailAlreadyRegistered(email) from exc

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
