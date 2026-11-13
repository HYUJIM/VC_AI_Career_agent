import json
import os
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

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


class OAuthAccountConflict(Exception):
    pass


def _insert_holder_key(cur, user_id: str, holder_key: Optional[StoredHolderKey], now: datetime) -> None:
    if holder_key is None:
        return
    cur.execute(
        """
        INSERT INTO holder_keys (user_id, did, key_type, public_key_multibase,
                                 encrypted_private_key, nonce, kek_id, created_at)
        VALUES (%s, %s, 'Ed25519', %s, %s, %s, %s, %s)
        """,
        (user_id, holder_key.did, holder_key.public_key_multibase,
         holder_key.encrypted_private_key, holder_key.nonce, holder_key.kek_id, now),
    )


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
                    _insert_holder_key(cur, user_id, holder_key, now)
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

    def get_user_oauth_providers(self, user_id: str) -> list[str]:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT provider FROM oauth_accounts WHERE user_id = %s ORDER BY provider",
                (user_id,),
            )
            return [row["provider"] for row in cur.fetchall()]

    def find_or_create_oauth_user(
        self,
        *,
        provider: str,
        provider_user_id: str,
        email: str,
        name: str,
        issue_identity: Callable[[str], tuple[str, Optional[StoredHolderKey]]],
    ) -> tuple[dict, bool, bool]:
        """OAuth 사용자를 조회·연동하거나 새로 생성한다. (user, created, linked) 반환."""
        for attempt in range(2):
            try:
                return self._find_or_create_oauth_user_once(
                    provider=provider,
                    provider_user_id=provider_user_id,
                    email=email,
                    name=name,
                    issue_identity=issue_identity,
                )
            except psycopg.errors.UniqueViolation as exc:
                constraint = exc.diag.constraint_name or ""
                if "user_id" in constraint and "provider" in constraint:
                    raise OAuthAccountConflict("User is already linked to another account for this provider") from exc
                if attempt == 1:
                    raise

    def _find_or_create_oauth_user_once(
        self,
        *,
        provider: str,
        provider_user_id: str,
        email: str,
        name: str,
        issue_identity: Callable[[str], tuple[str, Optional[StoredHolderKey]]],
    ) -> tuple[dict, bool, bool]:
        now = datetime.now(timezone.utc)
        with get_db_connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT u.*, oa.email AS oauth_email
                    FROM oauth_accounts oa
                    JOIN users u ON u.id = oa.user_id
                    WHERE oa.provider = %s AND oa.provider_user_id = %s
                    """,
                    (provider, provider_user_id),
                )
                linked_row = cur.fetchone()
                if linked_row:
                    if linked_row["oauth_email"] != email:
                        cur.execute(
                            "UPDATE oauth_accounts SET email = %s, updated_at = %s "
                            "WHERE provider = %s AND provider_user_id = %s",
                            (email, now, provider, provider_user_id),
                        )
                    linked_row.pop("oauth_email", None)
                    return linked_row, False, False

                cur.execute("SELECT * FROM users WHERE lower(email) = lower(%s)", (email,))
                existing_user = cur.fetchone()
                if existing_user:
                    cur.execute(
                        "SELECT provider_user_id FROM oauth_accounts WHERE user_id = %s AND provider = %s",
                        (existing_user["id"], provider),
                    )
                    existing_link = cur.fetchone()
                    if existing_link is not None:
                        raise OAuthAccountConflict("User is already linked to another account for this provider")
                    cur.execute(
                        """
                        INSERT INTO oauth_accounts (user_id, provider, provider_user_id, email, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (existing_user["id"], provider, provider_user_id, email, now, now),
                    )
                    return existing_user, False, True

                user_id = str(uuid.uuid4())
                did, holder_key = issue_identity(user_id)
                cur.execute(
                    """
                    INSERT INTO users (id, email, name, subject_did, password_hash, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, NULL, %s, %s)
                    RETURNING *
                    """,
                    (user_id, email, name, did, now, now),
                )
                new_user = cur.fetchone()
                _insert_holder_key(cur, user_id, holder_key, now)
                cur.execute(
                    """
                    INSERT INTO oauth_accounts (user_id, provider, provider_user_id, email, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (user_id, provider, provider_user_id, email, now, now),
                )
                return new_user, True, False

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
