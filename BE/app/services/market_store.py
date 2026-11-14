"""직무별 시장 통계 저장소 (12_market_statistics.sql).

- market_postings: 수집한 공고의 구간별 텍스트. 통계 재계산용 내부 자료라 API로 내보내지 않는다.
- market_snapshots: AI가 계산한 통계. 쌓기만 하고, 읽을 때마다 저장된 본문으로 content_hash를 다시 계산해 확인한다.
"""
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from app.ai_contract.market import MAX_POSTINGS, MarketPosting, MarketSnapshot
from app.services.career_store import digest_json
from app.services.database import get_db_connection

_SECTIONS = ("tech_stack", "responsibilities", "qualifications", "preferred")


def snapshot_hash(snapshot: MarketSnapshot) -> str:
    """content_hash를 뺀 나머지의 RFC 8785 SHA-256 (AI 서비스와 같은 식)."""
    payload = snapshot.model_dump(mode="json")
    payload.pop("content_hash")
    return digest_json(payload)


def _snapshot(row: dict) -> MarketSnapshot:
    try:
        snapshot = MarketSnapshot.model_validate(row["snapshot"])
    except ValidationError:
        raise HTTPException(status_code=409, detail="Market snapshot integrity check failed") from None
    if snapshot.content_hash != row["content_hash"].strip() or snapshot_hash(snapshot) != snapshot.content_hash:
        raise HTTPException(status_code=409, detail="Market snapshot integrity check failed")
    return snapshot


class MarketStore:

    def upsert_posting(self, role_id: str, posting: MarketPosting, rights_basis: str) -> str:
        """공고를 저장하고(같은 출처·번호면 내용 갱신) 직무를 덧붙인다. 'created' | 'updated' | 'unchanged'."""
        sections = {name: getattr(posting, name) for name in _SECTIONS}
        content_hash = digest_json({"company": posting.company, "title": posting.title, **sections})
        with get_db_connection() as conn, conn.transaction():
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
        with get_db_connection() as conn, conn.transaction():
            conn.execute("UPDATE market_postings SET role_ids = array_remove(role_ids, %s), updated_at = NOW()"
                         " WHERE source = %s AND external_id = %s AND role_ids @> %s",
                         (role_id, source, external_id, [role_id]))
            conn.execute("DELETE FROM market_postings WHERE source = %s AND external_id = %s AND role_ids = '{}'",
                         (source, external_id))

    def recent_posting(self, source: str, external_id: str, newer_than: datetime) -> Optional[MarketPosting]:
        """newer_than 이후에 수집해 둔 공고가 있으면 그것을 돌려준다 (같은 공고를 출처에 다시 요청하지 않으려고)."""
        with get_db_connection() as conn:
            row = conn.execute(
                """SELECT source, external_id, source_url, company, title, experience_min_years,
                          experience_max_years, closes_at, sections, captured_at
                   FROM market_postings WHERE source = %s AND external_id = %s AND captured_at >= %s""",
                (source, external_id, newer_than)).fetchone()
        if row is None:
            return None
        return MarketPosting(**{k: v for k, v in row.items() if k != "sections"}, **row["sections"])

    def postings(self, role_id: str) -> list[MarketPosting]:
        """그 직무의 공고 (최근에 수집한 것부터, AI 계약 한도까지)."""
        with get_db_connection() as conn:
            rows = conn.execute(
                """SELECT source, external_id, source_url, company, title, experience_min_years,
                          experience_max_years, closes_at, sections, captured_at
                   FROM market_postings WHERE role_ids @> %s
                   ORDER BY captured_at DESC, source, external_id LIMIT %s""",
                ([role_id], MAX_POSTINGS)).fetchall()
        return [MarketPosting(**{k: v for k, v in row.items() if k != "sections"}, **row["sections"]) for row in rows]

    def posting_counts(self) -> dict[str, int]:
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT role_id, count(*) AS n FROM market_postings, unnest(role_ids) AS role_id GROUP BY role_id"
            ).fetchall()
        return {row["role_id"]: row["n"] for row in rows}

    def save_snapshot(self, snapshot: MarketSnapshot) -> bool:
        """새 통계면 저장하고 True. 같은 내용(같은 해시)이 이미 있으면 False."""
        with get_db_connection() as conn:
            row = conn.execute(
                """INSERT INTO market_snapshots (role_id, as_of, sample_size, snapshot, content_hash)
                   VALUES (%s, %s, %s, %s, %s) ON CONFLICT (role_id, content_hash) DO NOTHING RETURNING id""",
                (snapshot.role_id, snapshot.as_of, snapshot.sample_size,
                 Jsonb(snapshot.model_dump(mode="json")), snapshot.content_hash)).fetchone()
        return row is not None

    def latest(self, role_id: str) -> Optional[MarketSnapshot]:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT snapshot, content_hash FROM market_snapshots WHERE role_id = %s"
                " ORDER BY as_of DESC, created_at DESC LIMIT 1", (role_id,)).fetchone()
        return _snapshot(row) if row else None

    def latest_all(self) -> list[MarketSnapshot]:
        with get_db_connection() as conn:
            rows = conn.execute(
                """SELECT DISTINCT ON (role_id) snapshot, content_hash FROM market_snapshots
                   ORDER BY role_id, as_of DESC, created_at DESC""").fetchall()
        return [_snapshot(row) for row in rows]


market_store = MarketStore()
