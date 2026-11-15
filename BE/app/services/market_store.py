"""직무별 시장 통계 저장소 (12_market_statistics.sql).

- market_postings: 수집한 공고의 구간별 텍스트. 통계 재계산용 내부 자료라 API로 내보내지 않는다.
- market_snapshots: AI가 계산한 통계. 쌓기만 하고, 읽을 때마다 저장된 본문으로 content_hash를 다시 계산해 확인한다.
"""
import logging
from datetime import datetime
from typing import Optional, get_args

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.ai_contract.market import MAX_POSTINGS, MarketPosting, MarketRoleID, MarketSnapshot
from app.services.career_store import digest_json
from app.services.database import app_db_connection

logger = logging.getLogger(__name__)

_SECTIONS = ("tech_stack", "responsibilities", "qualifications", "preferred")
# MarketPosting으로 되돌릴 때 읽는 열 (sections는 _posting이 구간별 필드로 푼다)
_POSTING_COLUMNS = ("source, external_id, source_url, company, title, experience_min_years, experience_max_years,"
                    " closes_at, sections, captured_at")


def snapshot_hash(snapshot: MarketSnapshot) -> str:
    """content_hash를 뺀 나머지의 RFC 8785 SHA-256 (AI 서비스와 같은 식)."""
    payload = snapshot.model_dump(mode="json")
    payload.pop("content_hash")
    return digest_json(payload)


def _snapshot(row: dict) -> MarketSnapshot:
    """저장된 통계 한 행 → MarketSnapshot. 형식이 틀리거나 해시가 맞지 않으면 409 (그 전에 직무와 행 ID를 로그로 남긴다)."""
    try:
        snapshot = MarketSnapshot.model_validate(row["snapshot"])
        # 해시 계산의 ValueError(RFC 8785가 다룰 수 없는 큰 정수 등)도 형식 오류처럼 무결성 실패로 본다 (500이 아니다).
        intact = (snapshot.content_hash == row["content_hash"].strip()
                  and snapshot_hash(snapshot) == snapshot.content_hash)
    except ValueError:  # 형식 검사 실패(pydantic ValidationError)도 ValueError다
        intact = False
    if not intact:
        # 409는 HTTPException이라 서버 로그에 남지 않는다: 어느 행인지 여기서 남긴다 (저장된 내용은 싣지 않는다).
        logger.warning("Market snapshot integrity check failed: role=%s row=%s", row["role_id"], row["id"])
        raise HTTPException(status_code=409, detail="Market snapshot integrity check failed")
    return snapshot


def _posting(row: dict) -> MarketPosting:
    return MarketPosting(**{k: v for k, v in row.items() if k != "sections"}, **row["sections"])


class MarketStore:

    def upsert_posting(self, role_id: str, posting: MarketPosting, rights_basis: str) -> str:
        """공고를 저장하고(같은 출처·번호면 내용 갱신) 직무를 덧붙인다. 'created' | 'updated' | 'unchanged'."""
        sections = {name: getattr(posting, name) for name in _SECTIONS}
        content_hash = digest_json({"company": posting.company, "title": posting.title, **sections})
        with app_db_connection() as conn, conn.transaction():
            row = conn.execute(
                "SELECT id, content_hash, role_ids FROM market_postings WHERE source = %s AND external_id = %s"
                " FOR UPDATE", (posting.source, posting.external_id)).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO market_postings (source, external_id, role_ids, source_url, company, title,
                           experience_min_years, experience_max_years, closes_at, sections, content_hash,
                           rights_basis, captured_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (posting.source, posting.external_id, [role_id], str(posting.source_url), posting.company,
                     posting.title, posting.experience_min_years, posting.experience_max_years, posting.closes_at,
                     Jsonb(sections), content_hash, rights_basis, posting.captured_at))
                return "created"
            roles = sorted(set(row["role_ids"]) | {role_id})
            changed = row["content_hash"].strip() != content_hash
            conn.execute(
                """UPDATE market_postings SET role_ids = %s, source_url = %s, company = %s, title = %s,
                       experience_min_years = %s, experience_max_years = %s, closes_at = %s, sections = %s,
                       content_hash = %s, rights_basis = %s, captured_at = %s, updated_at = NOW()
                   WHERE id = %s""",
                (roles, str(posting.source_url), posting.company, posting.title, posting.experience_min_years,
                 posting.experience_max_years, posting.closes_at, Jsonb(sections), content_hash, rights_basis,
                 posting.captured_at, row["id"]))
            return "updated" if changed or roles != sorted(row["role_ids"]) else "unchanged"

    def remove_role(self, source: str, external_id: str, role_id: str) -> None:
        """공고에서 직무 하나를 뺀다. 남는 직무가 없으면 공고를 지운다."""
        with app_db_connection() as conn, conn.transaction():
            conn.execute("UPDATE market_postings SET role_ids = array_remove(role_ids, %s), updated_at = NOW()"
                         " WHERE source = %s AND external_id = %s AND role_ids @> %s",
                         (role_id, source, external_id, [role_id]))
            conn.execute("DELETE FROM market_postings WHERE source = %s AND external_id = %s AND role_ids = '{}'",
                         (source, external_id))

    def recent_posting(self, source: str, external_id: str, newer_than: datetime) -> Optional[MarketPosting]:
        """newer_than 이후에 수집해 둔 공고가 있으면 그것을 돌려준다 (같은 공고를 출처에 다시 요청하지 않으려고)."""
        with app_db_connection() as conn:
            row = conn.execute(
                f"SELECT {_POSTING_COLUMNS} FROM market_postings"
                " WHERE source = %s AND external_id = %s AND captured_at >= %s",
                (source, external_id, newer_than)).fetchone()
        return _posting(row) if row else None

    def postings(self, role_id: str) -> list[MarketPosting]:
        """그 직무의 공고 (최근에 수집한 것부터, AI 계약 한도까지)."""
        with app_db_connection() as conn:
            rows = conn.execute(
                f"SELECT {_POSTING_COLUMNS} FROM market_postings WHERE role_ids @> %s"
                " ORDER BY captured_at DESC, source, external_id LIMIT %s",
                ([role_id], MAX_POSTINGS)).fetchall()
        return [_posting(row) for row in rows]

    def posting_counts(self) -> dict[str, int]:
        with app_db_connection() as conn:
            rows = conn.execute(
                "SELECT role_id, count(*) AS n FROM market_postings, unnest(role_ids) AS role_id GROUP BY role_id"
            ).fetchall()
        return {row["role_id"]: row["n"] for row in rows}

    def save_snapshot(self, snapshot: MarketSnapshot) -> bool:
        """새 통계면 저장하고 True. 같은 내용(같은 해시)이 이미 있으면 False."""
        with app_db_connection() as conn:
            row = conn.execute(
                """INSERT INTO market_snapshots (role_id, as_of, sample_size, snapshot, content_hash)
                   VALUES (%s, %s, %s, %s, %s) ON CONFLICT (role_id, content_hash) DO NOTHING RETURNING id""",
                (snapshot.role_id, snapshot.as_of, snapshot.sample_size,
                 Jsonb(snapshot.model_dump(mode="json")), snapshot.content_hash)).fetchone()
        return row is not None

    def latest(self, role_id: str) -> Optional[MarketSnapshot]:
        with app_db_connection() as conn:
            row = conn.execute(
                "SELECT id, role_id, snapshot, content_hash FROM market_snapshots WHERE role_id = %s"
                " ORDER BY as_of DESC, created_at DESC LIMIT 1", (role_id,)).fetchone()
        return _snapshot(row) if row else None

    def latest_all(self) -> list[MarketSnapshot]:
        """직무별 최신 통계. 이 빌드가 아는 직무만 읽는다 (다른 버전의 배치가 쓴 모르는 직무의 행 하나가 목록 전체를
        409로 만들지 않게). 아는 직무의 행이 검사를 통과하지 못하면 지금처럼 전체가 409다."""
        with app_db_connection() as conn:
            rows = conn.execute(
                """SELECT DISTINCT ON (role_id) id, role_id, snapshot, content_hash FROM market_snapshots
                   WHERE role_id = ANY(%s)
                   ORDER BY role_id, as_of DESC, created_at DESC""", (list(get_args(MarketRoleID)),)).fetchall()
        return [_snapshot(row) for row in rows]


market_store = MarketStore()
