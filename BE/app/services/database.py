import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, Iterator, Optional

import psycopg
from psycopg.rows import dict_row

from app.core.config import db_max_connections, db_password, db_tls_options
from app.domain.credential import UNVERIFIED_UPLOAD, BadgeEvaluation
from app.services.key_custody import StoredHolderKey


def get_db_connection(**extra):
    """상한·시간 제한 없이 바로 여는 연결. 마이그레이션 실행기(app.migrate)와 테스트가 인자 없이 쓴다.

    앱 요청은 이 함수 대신 아래 app_db_connection()을 쓴다 (extra는 그 접근자가 세션 options를 더할 때만 준다).
    TLS 인자는 db_tls_options()가 만든다: sslmode는 앞뒤 공백을 뗀 PGSSLMODE(비우면 libpq 기본값), CA 묶음은 libpq가
    PGSSLROOTCERT에서 읽는다. production은 인증서를 확인하는 설정이 아니면 접속하지 않는다 (db_password()와 같은 방식).
    """
    return psycopg.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "").strip() or 5432),  # `DB_PORT=`처럼 비워 둔 줄은 기본값
        user=os.getenv("DB_USER", "postgres"),
        # development는 지금처럼 없으면 로컬 기본값, production은 비었거나 로컬 기본값이면 접속하지 않는다.
        password=db_password(),
        dbname=os.getenv("DB_NAME", "vc_career_agent"),
        # DB에 닿지 않을 때 psycopg 기본값(130초)까지 기다리지 않고 빨리 실패한다 (초, 기본 10).
        connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT") or os.getenv("PGCONNECT_TIMEOUT") or 10),
        row_factory=dict_row,
        autocommit=True,
        **db_tls_options(),
        **extra,
    )


# ── 앱 요청용 DB 접근: 동시 연결 수 상한 + 세션 시간 제한 ─────────────────────
# 연결 풀이 아니다: 자리를 얻은 뒤 지금처럼 호출마다 새 연결을 열고 닫는다. 프로세스(BE 컨테이너)가 동시에 여는 연결 수만
# DB_MAX_CONNECTIONS(기본 20)로 묶는다. 묶지 않으면 한 계정의 병렬 요청이 워커 스레드 수(40)만큼 연결을 열어
# 프로세스 수 × 40이 DB max_connections를 넘고, 다른 사용자의 요청까지 'too many connections' 500이 된다.
# 자리가 없으면 이만큼(초) 기다린 뒤 DatabaseBusy다 (main.py가 503 + Retry-After로 답한다).
DB_SLOT_WAIT_SECONDS = 5.0
# FE가 이 문구로 안내를 고른다 (FE/src/lib/apiClient.ts). 바꾸면 FE도 함께 바꾼다.
DB_BUSY = "Service is busy. Try again later."
DB_BUSY_RETRY_AFTER_SECONDS = 5
# 앱 세션의 시간 제한 (ms). 잠긴 테이블 하나나 느린 쿼리 하나를 워커 스레드 전부가 끝없이 기다리지 않게 한다:
# 문장 15초(QueryCanceled), 잠금 대기 5초(LockNotAvailable. advisory lock 대기도 포함), 트랜잭션을 연 채 쉬는 세션 30초.
# 접속할 때 options로 주므로 왕복이 늘지 않고, 역할·DB에 건 설정(ALTER ROLE ... SET)보다 우선한다.
# 마이그레이션 실행기는 get_db_connection()을 그대로 써서 이 제한을 받지 않는다 (인덱스 생성 등은 오래 걸릴 수 있다).
APP_SESSION_OPTIONS = "-c statement_timeout=15000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=30000"


class DatabaseBusy(Exception):
    """앱 DB 연결 자리를 DB_SLOT_WAIT_SECONDS 안에 얻지 못했다. main.py가 503 + Retry-After로 답한다."""


class _ConnectionSlots:
    """프로세스가 동시에 여는 앱 DB 연결 자리. 처음 쓸 때 DB_MAX_CONNECTIONS를 읽어 만든다 (reset()은 테스트용)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._semaphore: Optional[threading.BoundedSemaphore] = None

    def acquire(self) -> threading.BoundedSemaphore:
        """자리 하나를 잡고, 돌려줄 때 release()할 세마포어를 돌려준다. 기다려도 자리가 없으면 DatabaseBusy."""
        with self._lock:
            if self._semaphore is None:
                self._semaphore = threading.BoundedSemaphore(db_max_connections())
            semaphore = self._semaphore
        if not semaphore.acquire(timeout=DB_SLOT_WAIT_SECONDS):
            raise DatabaseBusy(f"no free DB connection slot within {DB_SLOT_WAIT_SECONDS:g}s")
        return semaphore

    def reset(self) -> None:
        """다음 연결부터 DB_MAX_CONNECTIONS를 다시 읽는다. 이미 잡힌 자리는 예전 세마포어로 돌아가므로 서로 섞이지 않는다."""
        with self._lock:
            self._semaphore = None


app_db_slots = _ConnectionSlots()
_held = threading.local()  # 이 스레드가 지금 쥐고 있는 앱 연결 (중첩 사용 확인용)


def _app_session_options() -> str:
    # options를 직접 주면 libpq가 PGOPTIONS 환경변수를 읽지 않으므로 그 값을 앞에 그대로 잇는다.
    # 같은 설정이 겹치면 뒤에 온 값이 적용되므로 위 시간 제한이 항상 이긴다.
    return " ".join(part for part in (os.getenv("PGOPTIONS", "").strip(), APP_SESSION_OPTIONS) if part)


@contextmanager
def app_db_connection() -> Iterator[psycopg.Connection]:
    """앱 요청이 쓰는 DB 연결. `with app_db_connection() as conn:`으로만 쓰고, 블록이 끝나면 닫힌다.

    - 프로세스 전체에서 동시에 DB_MAX_CONNECTIONS개까지만 연다. 자리가 없으면 DB_SLOT_WAIT_SECONDS 동안 기다린 뒤
      DatabaseBusy를 던진다 (연결은 열지 않는다).
    - 세션에 APP_SESSION_OPTIONS(문장·잠금 대기·유휴 트랜잭션 시간 제한)를 건다.
    - 같은 스레드가 블록 안에서 다시 부르면 자리를 더 잡지 않고 바깥 블록의 연결을 그대로 돌려준다 (자기 자신을 기다리다
      멈추지 않는다). 그 연결은 바깥 블록이 끝날 때 닫히고, 안쪽의 conn.transaction()은 바깥 트랜잭션의 세이브포인트가 된다.
      지금 코드에는 중첩 사용이 없다 (모든 호출이 블록 하나로 끝난다).
    - 동기 코드에서만 쓰고 블록 안에서 await하지 않는다 (지금처럼 run_in_threadpool·동기 엔드포인트에서 부른다):
      "같은 스레드"로 중첩을 가리므로, 이벤트 루프 스레드에서 블록을 연 채 await하면 다른 코루틴이 그 연결을 함께 쓰게 된다.
    - 블록은 들어온 스레드에서 나간다. 다른 스레드에서 나가면(예: yield 의존성으로 쓴 경우) 들어온 스레드에 이미 닫힌 연결이
      남으므로, 남아 있는 연결이 닫혀 있으면 없는 것으로 보고 새 연결을 연다 (그 스레드의 다음 호출이 닫힌 연결을 받지 않게).
    """
    outer = getattr(_held, "conn", None)
    if outer is not None and not outer.closed:
        yield outer
        return
    slot = app_db_slots.acquire()
    try:
        with get_db_connection(options=_app_session_options()) as conn:
            _held.conn = conn
            try:
                yield conn
            finally:
                _held.conn = None
    finally:
        slot.release()


# 사용자 한 명이 만들 수 있는 수와 목록 조회가 한 번에 돌려주는 행 수의 상한. 없으면 한 계정이 행을 끝없이 만들고
# 그 목록(수 MiB)을 병렬로 읽어 다른 사용자의 요청까지 느리게 만든다.
MAX_ACTIVE_SHARES_PER_USER = 100  # 철회·만료되지 않은 공유 링크
MAX_PORTFOLIOS_PER_USER = 100
MAX_UNVERIFIED_UPLOADS_PER_USER = 10  # 격리 보관하는 '미검증 기록'(위조·변조 의심 업로드)
LIST_LIMIT = 200
# 모의 발급 기록의 '진행 중' 창 (초). 모든 사용자를 합친 전체 한도(reserve_mock_issuance)는 해시가 없는 기록(아직 협력사를
# 부르는 중인 요청, 결과를 모르는 실패)을 만든 뒤 이 시간 동안만 센다. 발급 호출 하나가 걸릴 수 있는 가장 긴 시간보다 길어야
# 한다: 협력사 동시 호출 자리 대기(DXWORKS_TIMEOUT_SECONDS × 2) + 교환(× 2). 설정 상한 120초에서도 4 × 120 + 60 = 540초 < 600초.
MOCK_ISSUANCE_PENDING_SECONDS = 10 * 60


class EmailAlreadyRegistered(Exception):
    pass


class OAuthAccountConflict(Exception):
    pass


# FE가 이 문구로 안내를 고른다 (FE/src/lib/apiClient.ts). 바꾸면 FE도 함께 바꾼다.
OAUTH_PASSWORD_ACCOUNT = "This email is registered with a password. Log in with your email and password."


class OAuthPasswordAccountConflict(OAuthAccountConflict):
    """OAuth로 인증된 이메일에 이미 비밀번호 계정이 있다 (그 계정에는 연동하지 않는다). user_id: 그 계정 (감사 로그용)."""

    def __init__(self, user_id: str):
        super().__init__(OAUTH_PASSWORD_ACCOUNT)
        self.user_id = user_id


# FE가 이 문구로 안내를 고른다 (FE/src/lib/apiClient.ts). 바꾸면 FE도 함께 바꾼다.
OAUTH_OTHER_METHOD_ACCOUNT = "This email is already used by another sign-in method. Log in with that method."
# 이메일을 인증했다고 알리는 OAuth 제공자 (app/integrations/oauth.py에서 requires_verified_email=True인 어댑터).
# 이 제공자의 연동은 인증된 이메일로만 만들어지고(api/oauth.py가 인증되지 않은 이메일을 400으로 거절한다) 로그인할 때마다
# 그 이메일로 갱신된다. 그래서 이 제공자 연동의 이메일이 계정 이메일과 같으면, 그 계정의 이메일은 주인이 확인된 주소다.
EMAIL_VERIFYING_OAUTH_PROVIDERS = ("google", "github")


class OAuthUnverifiedLinkConflict(OAuthAccountConflict):
    """같은 이메일의 비밀번호 없는 계정이 있지만 연동하지 않는다: 이번 제공자가 이메일을 인증하지 않았거나(Naver),
    그 계정의 이메일이 인증된 제공자 연동(EMAIL_VERIFYING_OAUTH_PROVIDERS)으로 확인된 주소가 아니다 (예: Naver로 만든 계정).
    user_id: 그 계정 (감사 로그용)."""

    def __init__(self, user_id: str):
        super().__init__(OAUTH_OTHER_METHOD_ACCOUNT)
        self.user_id = user_id


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
            with app_db_connection() as conn:
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
        with app_db_connection() as conn, conn.cursor() as cur:
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
        with app_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE lower(email) = lower(%s)", (email,))
            return cur.fetchone()

    def get_user_by_id(self, user_id: str) -> Optional[dict]:
        with app_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            return cur.fetchone()

    def get_user_by_did(self, did: str) -> Optional[dict]:
        with app_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE subject_did = %s", (did,))
            return cur.fetchone()

    def get_user_oauth_providers(self, user_id: str) -> list[str]:
        with app_db_connection() as conn, conn.cursor() as cur:
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
        email_verified: bool,
        name: str,
        issue_identity: Callable[[str], tuple[str, Optional[StoredHolderKey]]],
    ) -> tuple[dict, bool, bool]:
        """OAuth 사용자를 조회·연동하거나 새로 생성한다. (user, created, linked) 반환.

        1. 이 제공자 계정(provider, provider_user_id)이 이미 연동돼 있으면 그 계정으로 로그인한다.
        2. 같은 이메일의 계정이 있으면 비밀번호 계정(OAuthPasswordAccountConflict)과, 같은 제공자의 다른 계정이 이미
           연동된 계정(OAuthAccountConflict)에는 붙이지 않는다. 그 밖에는 email_verified가 True이고 그 계정의 이메일이
           인증된 제공자 연동으로 확인된 주소일 때만 연동한다 (아니면 OAuthUnverifiedLinkConflict).
        3. 같은 이메일의 계정이 없으면 새 계정을 만든다 (제공자와 관계없이).
        """
        for attempt in range(2):
            try:
                return self._find_or_create_oauth_user_once(
                    provider=provider,
                    provider_user_id=provider_user_id,
                    email=email,
                    email_verified=email_verified,
                    name=name,
                    issue_identity=issue_identity,
                )
            except psycopg.errors.UniqueViolation:
                # 동시에 온 다른 요청이 같은 행(사용자·연동)을 먼저 넣었다. 어느 제약에 걸렸든 한 번만 다시 본다:
                # 두 번째에는 커밋된 행이 보이므로 같은 제공자 계정이면 그 계정으로 로그인하고, 같은 제공자의 다른 계정이면
                # 아래의 명시적 검사가 OAuthAccountConflict를 낸다 (자동 생성된 제약 이름에 기대지 않는다).
                if attempt == 1:
                    raise

    def _find_or_create_oauth_user_once(
        self,
        *,
        provider: str,
        provider_user_id: str,
        email: str,
        email_verified: bool,
        name: str,
        issue_identity: Callable[[str], tuple[str, Optional[StoredHolderKey]]],
    ) -> tuple[dict, bool, bool]:
        now = datetime.now(timezone.utc)
        with app_db_connection() as conn:
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
                        if existing_link["provider_user_id"] == provider_user_id:
                            # 위의 provider·id 조회와 이 조회 사이에 같은 제공자 계정의 다른 요청(다른 탭의 같은 로그인)이
                            # 연동을 커밋했다. 다른 계정이 아니므로 충돌이 아니라 그 계정의 로그인이다.
                            return existing_user, False, False
                        raise OAuthAccountConflict("User is already linked to another account for this provider")
                    if existing_user["password_hash"] is not None:
                        # 비밀번호가 있는 계정에는 OAuth 로그인을 붙이지 않는다. 가입은 이메일 소유를 확인하지 않으므로,
                        # 남의 주소로 먼저 가입해 둔 사람이 있으면 OAuth로 로그인한 진짜 주인이 그 사람의 비밀번호와
                        # 세션이 그대로 살아 있는 계정에 들어가게 된다 (계정 선점). 이미 연동된 계정(위의 provider·id
                        # 조회)은 예전과 같다.
                        raise OAuthPasswordAccountConflict(str(existing_user["id"]))
                    # 비밀번호 없는 계정에 이메일이 같다는 이유로 붙이려면 두 쪽 모두 확인된 주소여야 한다:
                    # - 이번 제공자가 이메일을 인증했다 (email_verified, Naver는 항상 False).
                    # - 그 계정의 이메일이 인증된 제공자(Google·GitHub) 연동의 이메일과 같다. Naver로 만든 계정의 이메일은 Naver가
                    #   인증하지 않은, 사용자가 바꿀 수 있는 주소라 그 계정에는 다른 제공자를 붙이지 않는다 (붙이면 남의 주소로
                    #   Naver 계정을 만들어 둔 사람의 계정에 진짜 주인이 들어간다).
                    # Google만 있던 때에는 비밀번호 없는 계정이 모두 Google 연동 계정이었으므로 Google의 동작은 같다.
                    cur.execute(
                        "SELECT EXISTS (SELECT 1 FROM oauth_accounts WHERE user_id = %s AND provider = ANY(%s)"
                        " AND lower(email) = lower(%s)) AS attested",
                        (existing_user["id"], list(EMAIL_VERIFYING_OAUTH_PROVIDERS), existing_user["email"]),
                    )
                    if email_verified is not True or not cur.fetchone()["attested"]:
                        raise OAuthUnverifiedLinkConflict(str(existing_user["id"]))
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

    def get_token_version(self, user_id: str) -> Optional[int]:
        """액세스 토큰 검사용 현재 토큰 버전 (로그아웃할 때마다 1씩 오른다). 사용자가 없으면 None."""
        with app_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT token_version FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            return row["token_version"] if row else None

    def bump_token_version(self, user_id: str) -> None:
        """그 사용자에게 지금까지 발급한 액세스 토큰을 모든 탭·기기에서 한 번에 무효로 만든다 (로그아웃)."""
        with app_db_connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE users SET token_version = token_version + 1 WHERE id = %s", (user_id,))

    # ── 자격증명 ──────────────────────────────────────────────
    @staticmethod
    def _upsert_credential(cur, *, user_id: str, provider: str, evaluation: BadgeEvaluation, vc_jwt: str,
                           now: datetime) -> dict:
        """credentials 한 행과 그 verification_checks를 넣는다 (같은 사용자·출처·원본 ID면 갱신). 트랜잭션은 부른 쪽이 연다."""
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

    def save_badge(self, *, user_id: str, provider: str, evaluation: BadgeEvaluation, vc_jwt: str) -> dict:
        """검증 결과를 credentials(원본 vcJwt 포함)와 verification_checks에 한 트랜잭션으로 저장한다."""
        now = datetime.now(timezone.utc)
        with app_db_connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                return self._upsert_credential(cur, user_id=user_id, provider=provider, evaluation=evaluation,
                                               vc_jwt=vc_jwt, now=now)

    def save_unverified_upload(self, *, user_id: str, evaluation: BadgeEvaluation, vc_jwt: str) -> Optional[dict]:
        """검증기가 발급자 서명·신뢰를 확인하지 못한 업로드를 '미검증 기록'으로 격리 보관한다.

        evaluation은 credential.quarantine_evaluation의 결과다 (키 = 사용자 + 원본 해시, 상태 invalid). 출처는
        UNVERIFIED_UPLOAD로 따로 두어 진짜 배지 레코드와 겹치지 않는다. 사용자마다 MAX_UNVERIFIED_UPLOADS_PER_USER개까지
        보관하고(같은 원본을 다시 올리면 그 행을 갱신), 한도면 저장하지 않고 None을 돌려준다.
        같은 사용자의 동시 업로드는 create_portfolio처럼 advisory lock으로 줄을 세운다.
        """
        now = datetime.now(timezone.utc)
        with app_db_connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"unverified_uploads:{user_id}",))
                cur.execute(
                    "SELECT count(*) AS n, count(*) FILTER (WHERE source_external_id = %s) AS same"
                    " FROM credentials WHERE user_id = %s AND source_provider = %s",
                    (evaluation.external_id, user_id, UNVERIFIED_UPLOAD),
                )
                counts = cur.fetchone()
                if not counts["same"] and counts["n"] >= MAX_UNVERIFIED_UPLOADS_PER_USER:
                    return None
                return self._upsert_credential(cur, user_id=user_id, provider=UNVERIFIED_UPLOAD,
                                               evaluation=evaluation, vc_jwt=vc_jwt, now=now)

    def find_credential_owner(self, record_id: str) -> Optional[str]:
        with app_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT user_id FROM credentials WHERE record_id = %s", (record_id,))
            row = cur.fetchone()
            return str(row["user_id"]) if row else None

    def get_user_and_credentials(self, user_did: str):
        """FE 대시보드가 쓰는 careers 응답. 응답 형식은 FE `CareersResponse` 계약이므로 기존 필드는 바꾸지 않는다.

        unverified_upload: 올릴 때 검증되지 않아 격리 보관한 '미검증 기록'인지 (근거로 쓰지 않는다).
        """
        with app_db_connection() as conn, conn.cursor() as cur:
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
                    "unverified_upload": c["source_provider"] == UNVERIFIED_UPLOAD,
                }
                for c in credentials
            ],
        }


    # ── 실행 시점 재검증 ──────────────────────────────────────
    _CREDENTIAL_ROW = """
        SELECT c.record_id, c.user_id, c.raw_token, c.raw_data, c.status, c.issuer_name, c.achievement_name,
               c.issued_at, c.expires_at, c.source_provider, u.subject_did
        FROM credentials c JOIN users u ON u.id = c.user_id
    """

    def get_credential_rows(self, user_id: str, record_ids: Optional[list[str]] = None) -> list[dict]:
        with app_db_connection() as conn, conn.cursor() as cur:
            if record_ids is None:
                cur.execute(self._CREDENTIAL_ROW + " WHERE c.user_id = %s ORDER BY c.issued_at DESC", (user_id,))
            else:
                cur.execute(self._CREDENTIAL_ROW + " WHERE c.user_id = %s AND c.record_id = ANY(%s::uuid[])"
                            " ORDER BY c.issued_at DESC", (user_id, record_ids))
            return cur.fetchall()

    def get_credential_row(self, record_id: str) -> Optional[dict]:
        with app_db_connection() as conn, conn.cursor() as cur:
            cur.execute(self._CREDENTIAL_ROW + " WHERE c.record_id = %s", (record_id,))
            return cur.fetchone()

    def update_reverification(self, record_id: str, status: str, checks: list, vc: dict, verified_at) -> None:
        """재검증 결과 반영. 본문 사본(raw_data)도 서명 원본 기준으로 되돌린다."""
        checked_at = verified_at or datetime.now(timezone.utc)
        with app_db_connection() as conn:
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
                    [(record_id, c.name, c.result, c.detail, checked_at) for c in latest.values()],
                )

    # ── 모의 발급 기록 ────────────────────────────────────────
    def reserve_mock_issuance(self, user_id: str, template_id: str, daily_limit: int,
                              global_daily_limit: Optional[int] = None) -> tuple[Optional[str], Optional[str]]:
        """최근 24시간 발급 수가 한도 안이면 발급 기록을 먼저 남기고 (그 id, None)을 돌려준다.

        한도에 닿았으면 기록을 남기지 않고 (None, 막은 한도)를 돌려준다: "user"(사용자별 한도) 또는 "global"(전체 한도).
        둘 다 찼으면 "user"다 (사용자별 한도를 먼저 본다). 호출한 쪽이 두 경우를 다른 문구로 답한다 (api/mock_issuer.py).
        협력사를 부르기 전에 자리를 잡는다. 같은 사용자의 동시 요청(여러 프로세스 포함)은 트랜잭션 advisory lock으로
        줄을 세워 한도를 넘지 못하게 한다. 발급에 실패하면 cancel_mock_issuance로 자리를 돌려준다.
        global_daily_limit을 주면 모든 사용자를 합친 발급 수도 같은 트랜잭션에서 센다 (계정을 여러 개 만들어
        사용자별 한도를 곱하지 못하게). 전체 잠금(고정 키) → 사용자 잠금 순서로만 잡는다 (순서가 같아야 서로 막히지 않는다).
        - 사용자별 한도: 최근 24시간의 기록을 모두 센다 (결과를 모르는 실패도 그 사용자의 하루 발급 하나를 쓴다).
        - 전체 한도: 최근 24시간의 성공 기록(해시가 남은 행)과, 상태와 관계없이 최근 MOCK_ISSUANCE_PENDING_SECONDS 안에 만든
          기록(아직 협력사를 부르는 중인 요청, 막 끝난 결과를 모르는 실패)을 센다.
        """
        with app_db_connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                # 잠금을 얻은 뒤의 count가 앞 요청이 커밋한 기록을 보도록 이 트랜잭션은 READ COMMITTED로 고정한다.
                # DB·역할·파라미터 그룹의 기본값이 REPEATABLE READ면 잠금을 기다리기 전에 잡은 스냅샷으로 세어 한도를 넘고,
                # SERIALIZABLE이면 직렬화 오류(500)가 난다. 트랜잭션의 첫 문장이어야 한다.
                cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("mock_issuance:global",))
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"mock_issuance:{user_id}",))
                cur.execute(
                    "SELECT count(*) AS n FROM mock_issuances"
                    " WHERE user_id = %s AND created_at > NOW() - INTERVAL '24 hours'",
                    (user_id,),
                )
                if cur.fetchone()["n"] >= daily_limit:
                    return None, "user"
                if global_daily_limit is not None:
                    # 결과를 모르는 실패(응답 대기 중 시간 초과 등)를 24시간 동안 세면, 협력사가 응답하지 않는 장애 한 번으로
                    # 전체 한도가 차서 협력사가 돌아와도 하루 동안 아무도 발급하지 못한다. 그래서 해시가 없는 기록은 진행 중 창
                    # 동안만 센다. 받아들인 결과: 발급됐지만 해시를 남기지 못한 배지(complete_mock_issuance 실패, 로그에 남는다)와
                    # 결과를 모르는 실패는 창이 지나면 전체 한도에 세지 않는다. 결과를 모르는 실패의 양은 실패 차단기
                    # (verifier_budget.FailureBreaker)와 사용자별 시도 한도가 묶는다. 해시를 남기지 못한 배지는 차단기가 성공으로
                    # 보므로, 창이 지난 뒤에는 사용자별 하루 발급 한도(MOCK_ISSUER_DAILY_LIMIT) × 계정 수만 묶는다.
                    cur.execute(
                        "SELECT count(*) AS n FROM mock_issuances WHERE created_at > NOW() - INTERVAL '24 hours'"
                        " AND (token_sha256 IS NOT NULL OR created_at > NOW() - make_interval(secs => %s))",
                        (MOCK_ISSUANCE_PENDING_SECONDS,),
                    )
                    if cur.fetchone()["n"] >= global_daily_limit:
                        return None, "global"
                cur.execute("INSERT INTO mock_issuances (user_id, template_id) VALUES (%s, %s) RETURNING id",
                            (user_id, template_id))
                return str(cur.fetchone()["id"]), None

    def complete_mock_issuance(self, issuance_id: str, token_sha256: str) -> None:
        """발급에 성공한 기록에 VC-JWT의 SHA-256(hex)을 남긴다 (원문은 저장하지 않는다).

        해시가 남은 기록은 전체 한도에도 24시간 동안 센다 (reserve_mock_issuance).
        """
        with app_db_connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE mock_issuances SET token_sha256 = %s WHERE id = %s", (token_sha256, issuance_id))

    def cancel_mock_issuance(self, issuance_id: str) -> None:
        """협력사 발급이 실패한 기록을 지운다 (실패한 요청은 한도에 세지 않는다)."""
        with app_db_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM mock_issuances WHERE id = %s", (issuance_id,))

    # ── 포트폴리오 ────────────────────────────────────────────
    def portfolio_limit_reached(self, user_id: str) -> bool:
        """이미 MAX_PORTFOLIOS_PER_USER개를 만들었는지 (만들기 전에 미리 보는 값. 동시 요청까지 막는 검사는 create_portfolio가 한다)."""
        with app_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM portfolios WHERE user_id = %s", (user_id,))
            return cur.fetchone()["n"] >= MAX_PORTFOLIOS_PER_USER

    def create_portfolio(self, *, user_id: str, title: str, target_role: Optional[str], bio_summary: str,
                         content_markdown: str, sections: dict, referenced_record_ids: list[str]) -> Optional[dict]:
        """포트폴리오를 저장하고 그 행을, 이미 MAX_PORTFOLIOS_PER_USER개가 있으면 저장하지 않고 None을 돌려준다.

        같은 사용자의 동시 요청(여러 프로세스 포함)은 reserve_mock_issuance처럼 트랜잭션 advisory lock으로 줄을 세워
        세기와 넣기 사이에 다른 요청이 끼어들지 못하게 한다.
        """
        now = datetime.now(timezone.utc)
        with app_db_connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                # 잠금을 얻은 뒤의 count가 앞 요청이 커밋한 행을 보도록 READ COMMITTED로 고정한다 (reserve_mock_issuance 참고).
                cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"portfolios:{user_id}",))
                cur.execute("SELECT count(*) AS n FROM portfolios WHERE user_id = %s", (user_id,))
                if cur.fetchone()["n"] >= MAX_PORTFOLIOS_PER_USER:
                    return None
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
        """목록용 요약 행 (최근에 고친 것부터 LIST_LIMIT개). 본문·근거(content_markdown·sections)는 읽지 않는다."""
        with app_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, target_role, status, referenced_record_ids, created_at, updated_at"
                " FROM portfolios WHERE user_id = %s ORDER BY updated_at DESC LIMIT %s",
                (user_id, LIST_LIMIT),
            )
            return cur.fetchall()

    def get_portfolio(self, user_id: str, portfolio_id: str) -> Optional[dict]:
        with app_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM portfolios WHERE id = %s AND user_id = %s", (portfolio_id, user_id))
            return cur.fetchone()

    def update_portfolio(self, user_id: str, portfolio_id: str, fields: dict) -> Optional[dict]:
        allowed = {k: v for k, v in fields.items() if k in ("title", "target_role", "status", "content_markdown")}
        if not allowed:
            return self.get_portfolio(user_id, portfolio_id)
        assignments = ", ".join(f"{k} = %s" for k in allowed)
        with app_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE portfolios SET {assignments}, updated_at = %s WHERE id = %s AND user_id = %s RETURNING *",
                (*allowed.values(), datetime.now(timezone.utc), portfolio_id, user_id),
            )
            return cur.fetchone()

    def replace_portfolio_body(self, user_id: str, portfolio_id: str, *, bio_summary: str, content_markdown: str,
                               sections: dict, referenced_record_ids: list[str]) -> Optional[dict]:
        """다시 쓰기: 본문·요약·근거만 바꾼다 (제목·희망 직무·상태는 그대로). 본인 행이 아니면 None."""
        with app_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE portfolios SET bio_summary = %s, content_markdown = %s, sections = %s,
                                      referenced_record_ids = %s::uuid[], updated_at = %s
                WHERE id = %s AND user_id = %s RETURNING *
                """,
                (bio_summary, content_markdown, json.dumps(sections), referenced_record_ids,
                 datetime.now(timezone.utc), portfolio_id, user_id),
            )
            return cur.fetchone()

    def delete_portfolio(self, user_id: str, portfolio_id: str) -> bool:
        with app_db_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM portfolios WHERE id = %s AND user_id = %s", (portfolio_id, user_id))
            return cur.rowcount > 0

    # ── 선택 공개 ─────────────────────────────────────────────
    def create_share(self, *, user_id: str, token_hash: str, record_ids: list[str], label: Optional[str],
                     expires_at) -> Optional[dict]:
        """공유 링크를 저장하고 그 행을, 유효한(철회·만료되지 않은) 링크가 이미 MAX_ACTIVE_SHARES_PER_USER개면 저장하지 않고
        None을 돌려준다. 철회하거나 만료된 링크는 세지 않으므로 쓰지 않는 링크를 철회하면 다시 만들 수 있다.

        같은 사용자의 동시 요청은 create_portfolio처럼 트랜잭션 advisory lock으로 줄을 세운다.
        """
        with app_db_connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"shares:{user_id}",))
                cur.execute(
                    "SELECT count(*) AS n FROM shares"
                    " WHERE user_id = %s AND revoked_at IS NULL AND expires_at > NOW()",
                    (user_id,),
                )
                if cur.fetchone()["n"] >= MAX_ACTIVE_SHARES_PER_USER:
                    return None
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
        """공유 링크 목록 (최근에 만든 것부터 LIST_LIMIT개).

        LIST_LIMIT개를 넘으면 유효한(철회·만료되지 않은) 링크를 먼저 고른 뒤 최근에 만든 순으로 돌려준다: 철회·만료된 링크가
        많이 쌓여도 아직 열리는 링크가 목록에서 밀려나 철회할 수 없게 되지 않는다. 넘지 않으면 예전과 같은 결과다.
        """
        with app_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM (
                    SELECT * FROM shares WHERE user_id = %s
                    ORDER BY (revoked_at IS NULL AND expires_at > NOW()) DESC, created_at DESC
                    LIMIT %s
                ) AS newest
                ORDER BY created_at DESC
                """,
                (user_id, LIST_LIMIT),
            )
            return cur.fetchall()

    def revoke_share(self, user_id: str, share_id: str) -> bool:
        with app_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE shares SET revoked_at = COALESCE(revoked_at, NOW()) WHERE id = %s AND user_id = %s",
                (share_id, user_id),
            )
            return cur.rowcount > 0

    def open_share(self, token_hash: str) -> Optional[dict]:
        """유효한(철회·만료되지 않은) 공유를 찾고 열람 수를 올린다."""
        with app_db_connection() as conn, conn.cursor() as cur:
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
