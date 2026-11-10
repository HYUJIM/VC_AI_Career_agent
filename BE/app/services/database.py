import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import psycopg
from psycopg.rows import dict_row

from app.core.config import db_password
from app.domain.credential import BadgeEvaluation
from app.services.key_custody import StoredHolderKey


def get_db_connection():
    return psycopg.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        user=os.getenv("DB_USER", "postgres"),
        # development는 지금처럼 없으면 로컬 기본값, production은 비었거나 로컬 기본값이면 접속하지 않는다.
        password=db_password(),
        dbname=os.getenv("DB_NAME", "vc_career_agent"),
        # DB에 닿지 않을 때 psycopg 기본값(130초)까지 기다리지 않고 빨리 실패한다 (초, 기본 10).
        connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT") or os.getenv("PGCONNECT_TIMEOUT") or 10),
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

    def get_token_version(self, user_id: str) -> Optional[int]:
        """액세스 토큰 검사용 현재 토큰 버전 (로그아웃할 때마다 1씩 오른다). 사용자가 없으면 None."""
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT token_version FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            return row["token_version"] if row else None

    def bump_token_version(self, user_id: str) -> None:
        """그 사용자에게 지금까지 발급한 액세스 토큰을 모든 탭·기기에서 한 번에 무효로 만든다 (로그아웃)."""
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE users SET token_version = token_version + 1 WHERE id = %s", (user_id,))

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
        """FE 대시보드가 쓰는 careers 응답. 응답 형식은 FE `CareersResponse` 계약이므로 바꾸지 않는다."""
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


    # ── 실행 시점 재검증 ──────────────────────────────────────
    _CREDENTIAL_ROW = """
        SELECT c.record_id, c.user_id, c.raw_token, c.raw_data, c.status, c.issuer_name, c.achievement_name,
               c.issued_at, c.expires_at, u.subject_did
        FROM credentials c JOIN users u ON u.id = c.user_id
    """

    def get_credential_rows(self, user_id: str, record_ids: Optional[list[str]] = None) -> list[dict]:
        with get_db_connection() as conn, conn.cursor() as cur:
            if record_ids is None:
                cur.execute(self._CREDENTIAL_ROW + " WHERE c.user_id = %s ORDER BY c.issued_at DESC", (user_id,))
            else:
                cur.execute(self._CREDENTIAL_ROW + " WHERE c.user_id = %s AND c.record_id = ANY(%s::uuid[])"
                            " ORDER BY c.issued_at DESC", (user_id, record_ids))
            return cur.fetchall()

    def get_credential_row(self, record_id: str) -> Optional[dict]:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(self._CREDENTIAL_ROW + " WHERE c.record_id = %s", (record_id,))
            return cur.fetchone()

    def update_reverification(self, record_id: str, status: str, checks: list, vc: dict, verified_at) -> None:
        """재검증 결과 반영. 본문 사본(raw_data)도 서명 원본 기준으로 되돌린다."""
        with get_db_connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    "UPDATE credentials SET status = %s, raw_data = %s, last_verified_at = %s WHERE record_id = %s",
                    (status, json.dumps(vc), verified_at, record_id),
                )
                cur.execute("DELETE FROM verification_checks WHERE record_id = %s", (record_id,))
                # 같은 항목이 두 번 나오면(재검증 가드가 덧붙인 fail) 마지막 결과를 남긴다.
                latest = {c.name: c for c in checks}
                cur.executemany(
                    "INSERT INTO verification_checks (record_id, check_name, result, detail, checked_at)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    [(record_id, c.name, c.result, c.detail, verified_at) for c in latest.values()],
                )

    # ── 모의 발급 기록 ────────────────────────────────────────
    def reserve_mock_issuance(self, user_id: str, template_id: str, daily_limit: int) -> Optional[str]:
        """최근 24시간 발급 수가 한도 안이면 발급 기록을 먼저 남기고 그 id를, 한도에 닿았으면 None을 돌려준다.

        협력사를 부르기 전에 자리를 잡는다. 같은 사용자의 동시 요청(여러 ECS 태스크 포함)은 트랜잭션 advisory lock으로
        줄을 세워 한도를 넘지 못하게 한다. 발급에 실패하면 cancel_mock_issuance로 자리를 돌려준다.
        """
        with get_db_connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                # 잠금을 얻은 뒤의 count가 앞 요청이 커밋한 기록을 보도록 이 트랜잭션은 READ COMMITTED로 고정한다.
                # DB·역할·파라미터 그룹의 기본값이 REPEATABLE READ면 잠금을 기다리기 전에 잡은 스냅샷으로 세어 한도를 넘고,
                # SERIALIZABLE이면 직렬화 오류(500)가 난다. 트랜잭션의 첫 문장이어야 한다.
                cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"mock_issuance:{user_id}",))
                cur.execute(
                    "SELECT count(*) AS n FROM mock_issuances"
                    " WHERE user_id = %s AND created_at > NOW() - INTERVAL '24 hours'",
                    (user_id,),
                )
                if cur.fetchone()["n"] >= daily_limit:
                    return None
                cur.execute("INSERT INTO mock_issuances (user_id, template_id) VALUES (%s, %s) RETURNING id",
                            (user_id, template_id))
                return str(cur.fetchone()["id"])

    def complete_mock_issuance(self, issuance_id: str, token_sha256: str) -> None:
        """발급에 성공한 기록에 VC-JWT의 SHA-256(hex)을 남긴다 (원문은 저장하지 않는다)."""
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE mock_issuances SET token_sha256 = %s WHERE id = %s", (token_sha256, issuance_id))

    def cancel_mock_issuance(self, issuance_id: str) -> None:
        """협력사 발급이 실패한 기록을 지운다 (실패한 요청은 한도에 세지 않는다)."""
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM mock_issuances WHERE id = %s", (issuance_id,))

    # ── 포트폴리오 ────────────────────────────────────────────
    def create_portfolio(self, *, user_id: str, title: str, target_role: Optional[str], bio_summary: str,
                         content_markdown: str, sections: dict, referenced_record_ids: list[str]) -> dict:
        now = datetime.now(timezone.utc)
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO portfolios (user_id, title, target_role, bio_summary, content_markdown, sections,
                                        referenced_record_ids, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s::uuid[], 'draft', %s, %s)
                RETURNING *
                """,
                (user_id, title, target_role, bio_summary, content_markdown, json.dumps(sections),
                 referenced_record_ids, now, now),
            )
            return cur.fetchone()

    def list_portfolios(self, user_id: str) -> list[dict]:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM portfolios WHERE user_id = %s ORDER BY updated_at DESC", (user_id,))
            return cur.fetchall()

    def get_portfolio(self, user_id: str, portfolio_id: str) -> Optional[dict]:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM portfolios WHERE id = %s AND user_id = %s", (portfolio_id, user_id))
            return cur.fetchone()

    def update_portfolio(self, user_id: str, portfolio_id: str, fields: dict) -> Optional[dict]:
        allowed = {k: v for k, v in fields.items() if k in ("title", "target_role", "status", "content_markdown")}
        if not allowed:
            return self.get_portfolio(user_id, portfolio_id)
        assignments = ", ".join(f"{k} = %s" for k in allowed)
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE portfolios SET {assignments}, updated_at = %s WHERE id = %s AND user_id = %s RETURNING *",
                (*allowed.values(), datetime.now(timezone.utc), portfolio_id, user_id),
            )
            return cur.fetchone()

    def delete_portfolio(self, user_id: str, portfolio_id: str) -> bool:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM portfolios WHERE id = %s AND user_id = %s", (portfolio_id, user_id))
            return cur.rowcount > 0

    # ── 선택 공개 ─────────────────────────────────────────────
    def create_share(self, *, user_id: str, token_hash: str, record_ids: list[str], label: Optional[str],
                     expires_at) -> dict:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO shares (user_id, token_hash, record_ids, label, expires_at)
                VALUES (%s, %s, %s::uuid[], %s, %s)
                RETURNING *
                """,
                (user_id, token_hash, record_ids, label, expires_at),
            )
            return cur.fetchone()

    def list_shares(self, user_id: str) -> list[dict]:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM shares WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
            return cur.fetchall()

    def revoke_share(self, user_id: str, share_id: str) -> bool:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE shares SET revoked_at = COALESCE(revoked_at, NOW()) WHERE id = %s AND user_id = %s",
                (share_id, user_id),
            )
            return cur.rowcount > 0

    def open_share(self, token_hash: str) -> Optional[dict]:
        """유효한(철회·만료되지 않은) 공유를 찾고 열람 수를 올린다."""
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE shares SET view_count = view_count + 1, last_viewed_at = NOW()
                WHERE token_hash = %s AND revoked_at IS NULL AND expires_at > NOW()
                RETURNING *
                """,
                (token_hash,),
            )
            return cur.fetchone()

db_service = DatabaseService()
