"""직무 비교·커리큘럼 저장소 (08_career_curriculum.sql).

공고·학습자료는 버전으로만 쌓고 덮어쓰지 않는다. 읽을 때마다 저장된 본문으로 content_hash를 다시 계산해
행과 맞는지 확인하고, 분석·커리큘럼 저장 직전에는 인용한 버전이 아직 최신인지 같은 트랜잭션에서 확인한다.
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import rfc8785
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from app.ai_contract.career import CareerAnalysis, JobVersion
from app.ai_contract.curriculum import CurriculumRequest, CurriculumResult, LearningResourceVersion
from app.services.database import get_db_connection

MAX_CANDIDATES = 1000  # AI 계약의 한도와 같다.
MAX_RESOURCES = 500

# 사용자 조회 응답에서 빼는 반입 내부 정보: 반입한 연구자 계정 ID, 검토 메모, 권리 근거, 원문 전체.
# 저장본·content_hash·AI로 보내는 계약 객체는 그대로 두고 응답으로 내보낼 때만 뺀다 (연구자 반입 응답은 전체).
_SOURCE_INTERNAL = {"rights_basis", "review_note", "raw_text"}
JOB_INTERNAL_FIELDS = {"reviewed_by": True, "posting": _SOURCE_INTERNAL}
RESOURCE_INTERNAL_FIELDS = {"reviewed_by": True, "resource": _SOURCE_INTERNAL}
_CURRICULUM_INTERNAL_FIELDS = {"steps": {"__all__": {"resource": RESOURCE_INTERNAL_FIELDS}}}


def digest_json(value: Any) -> str:
    """RFC 8785 정규화 JSON의 SHA-256. AI 서비스도 같은 방식으로 대조한다."""
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _job(row: dict) -> JobVersion:
    try:
        version = JobVersion.model_validate(row["payload"])
    except ValidationError:
        raise HTTPException(status_code=409, detail="Job source integrity check failed") from None
    if (str(version.version_id) != str(row["id"]) or str(version.posting_id) != str(row["posting_id"])
            or version.content_hash != row["content_hash"]
            or digest_json(version.posting.model_dump(mode="json")) != row["content_hash"]):
        raise HTTPException(status_code=409, detail="Job source integrity check failed")
    return version


def _resource(row: dict) -> LearningResourceVersion:
    try:
        version = LearningResourceVersion.model_validate(row["payload"])
    except ValidationError:
        raise HTTPException(status_code=409, detail="Learning source integrity check failed") from None
    if (str(version.version_id) != str(row["id"]) or str(version.resource_id) != str(row["resource_id"])
            or version.content_hash != row["content_hash"]
            or digest_json(version.resource.model_dump(mode="json")) != row["content_hash"]):
        raise HTTPException(status_code=409, detail="Learning source integrity check failed")
    return version


class CareerStore:
    # ── 공고 ──────────────────────────────────────────────────
    def import_job(self, posting, user_id: str) -> JobVersion:
        now = datetime.now(timezone.utc)
        content = posting.model_dump(mode="json")
        with get_db_connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """INSERT INTO job_postings (source, external_id) VALUES (%s, %s)
                ON CONFLICT (source, external_id) DO UPDATE SET source = EXCLUDED.source RETURNING id""",
                (posting.source, posting.external_id),
            )
            posting_id = cur.fetchone()["id"]
            version = JobVersion(posting_id=posting_id, version_id=uuid.uuid4(), imported_at=now,
                                 reviewed_by=user_id, content_hash=digest_json(content), posting=posting)
            cur.execute(
                """INSERT INTO job_posting_versions (id, posting_id, payload, content_hash, imported_by, imported_at)
                VALUES (%s, %s, %s, %s, %s, %s)""",
                (version.version_id, posting_id, Jsonb(version.model_dump(mode="json")), version.content_hash,
                 user_id, now),
            )
            cur.execute("UPDATE job_postings SET latest_version_id = %s WHERE id = %s",
                        (version.version_id, posting_id))
        return version

    _LATEST_JOB = """SELECT v.* FROM job_postings p
        JOIN job_posting_versions v ON v.id = p.latest_version_id AND v.posting_id = p.id"""

    def get_job(self, posting_id: str, version_id: Optional[str] = None) -> JobVersion:
        with get_db_connection() as conn, conn.cursor() as cur:
            if version_id:
                cur.execute("SELECT * FROM job_posting_versions WHERE posting_id = %s AND id = %s",
                            (posting_id, version_id))
            else:
                cur.execute(self._LATEST_JOB + " WHERE p.id = %s", (posting_id,))
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job posting not found")
        return _job(row)

    def market_candidates(self, role_id: str, now: datetime) -> list[JobVersion]:
        # 로컬 검토 공고 집합을 좁힐 뿐 시장 전체 표본이 아니다. 최종 선별은 AI가 다시 한다.
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                self._LATEST_JOB + """ WHERE v.payload->'posting'->>'role_id' = %s
                AND v.payload->'posting'->>'dataset_kind' = 'real'
                AND (v.payload->'posting'->>'posted_at')::timestamptz >= %s
                ORDER BY v.imported_at DESC, v.id LIMIT %s""",
                (role_id, now - timedelta(days=90), MAX_CANDIDATES + 1),
            )
            rows = cur.fetchall()
        if len(rows) > MAX_CANDIDATES:
            raise HTTPException(status_code=422, detail="Local corpus is too large; narrow the job sources")
        return [_job(row) for row in rows]

    # ── 학습자료 ──────────────────────────────────────────────
    def import_resource(self, resource, user_id: str) -> LearningResourceVersion:
        now = datetime.now(timezone.utc)
        content = resource.model_dump(mode="json")
        with get_db_connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """INSERT INTO learning_resources (source, external_id) VALUES (%s, %s)
                ON CONFLICT (source, external_id) DO UPDATE SET source = EXCLUDED.source RETURNING id""",
                (resource.source, resource.external_id),
            )
            resource_id = cur.fetchone()["id"]
            version = LearningResourceVersion(resource_id=resource_id, version_id=uuid.uuid4(), imported_at=now,
                                              reviewed_by=user_id, content_hash=digest_json(content),
                                              resource=resource)
            cur.execute(
                """INSERT INTO learning_resource_versions
                (id, resource_id, payload, content_hash, imported_by, imported_at) VALUES (%s, %s, %s, %s, %s, %s)""",
                (version.version_id, resource_id, Jsonb(version.model_dump(mode="json")), version.content_hash,
                 user_id, now),
            )
            cur.execute("UPDATE learning_resources SET latest_version_id = %s WHERE id = %s",
                        (version.version_id, resource_id))
        return version

    _LATEST_RESOURCE = """SELECT v.* FROM learning_resources r
        JOIN learning_resource_versions v ON v.id = r.latest_version_id AND v.resource_id = r.id"""

    def resources(self) -> list[LearningResourceVersion]:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(self._LATEST_RESOURCE + " ORDER BY r.id LIMIT %s", (MAX_RESOURCES + 1,))
            rows = cur.fetchall()
        if len(rows) > MAX_RESOURCES:
            raise HTTPException(status_code=422, detail="Learning catalog exceeds supported corpus size")
        return [_resource(row) for row in rows]

    def get_resource(self, resource_id: str, version_id: Optional[str] = None) -> LearningResourceVersion:
        with get_db_connection() as conn, conn.cursor() as cur:
            if version_id:
                cur.execute("SELECT * FROM learning_resource_versions WHERE resource_id = %s AND id = %s",
                            (resource_id, version_id))
            else:
                cur.execute(self._LATEST_RESOURCE + " WHERE r.id = %s", (resource_id,))
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Learning resource not found")
        return _resource(row)

    # ── 직무 비교 ─────────────────────────────────────────────
    def save_analysis(self, user_id: str, analysis: CareerAnalysis, snapshot) -> None:
        with get_db_connection() as conn, conn.transaction(), conn.cursor() as cur:
            ids = [str(source.posting_id) for source in analysis.sources]
            cur.execute(self._LATEST_JOB + " WHERE p.id = ANY(%s::uuid[]) FOR SHARE OF p", (ids,))
            current = {str(row["posting_id"]): row for row in cur.fetchall()}
            for source in analysis.sources:
                row = current.get(str(source.posting_id))
                if not row or str(row["id"]) != str(source.version_id) or row["content_hash"] != source.content_hash:
                    raise HTTPException(status_code=409, detail="Job source changed during analysis; retry")
            cur.execute(
                "INSERT INTO career_analyses (id, user_id, analysis, snapshot) VALUES (%s, %s, %s, %s)",
                (analysis.analysis_id, user_id, Jsonb(analysis.model_dump(mode="json")),
                 Jsonb(snapshot.model_dump(mode="json"))),
            )

    def get_analysis(self, user_id: str, analysis_id: str) -> CareerAnalysis:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM career_analyses WHERE id = %s AND user_id = %s", (analysis_id, user_id))
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Analysis not found")
        try:
            analysis = CareerAnalysis.model_validate(row["analysis"])
        except ValidationError:
            raise HTTPException(status_code=409, detail="Stored analysis integrity check failed") from None
        if str(analysis.analysis_id) != str(row["id"]):
            raise HTTPException(status_code=409, detail="Stored analysis integrity check failed")
        return analysis

    # ── 커리큘럼 ──────────────────────────────────────────────
    def find_curriculum(self, user_id: str, idempotency_key: str) -> Optional[dict]:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM curricula WHERE user_id = %s AND idempotency_key = %s",
                        (user_id, idempotency_key))
            return cur.fetchone()

    def save_curriculum(self, *, user_id: str, request: CurriculumRequest, result: CurriculumResult,
                        audit: list[dict]) -> dict:
        with get_db_connection() as conn, conn.transaction(), conn.cursor() as cur:
            used = sorted({str(step.resource.resource_id) for step in result.steps if step.resource})
            if used:
                cur.execute(self._LATEST_RESOURCE + " WHERE r.id = ANY(%s::uuid[]) FOR SHARE OF r", (used,))
                latest = {str(row["resource_id"]): str(row["id"]) for row in cur.fetchall()}
                for step in result.steps:
                    if step.resource and latest.get(str(step.resource.resource_id)) != str(step.resource.version_id):
                        raise HTTPException(status_code=409, detail="Learning source changed; create the plan again")
            cur.execute(
                """INSERT INTO curricula (id, user_id, analysis_id, idempotency_key, request, result, audit)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, idempotency_key) DO NOTHING RETURNING *""",
                (result.job_id, user_id, result.analysis_id, request.idempotency_key,
                 Jsonb(request.model_dump(mode="json")), Jsonb(result.model_dump(mode="json")), Jsonb(audit)),
            )
            row = cur.fetchone()
        if row is None:  # 같은 키로 동시에 들어온 요청이 먼저 저장했다.
            row = self.find_curriculum(user_id, str(request.idempotency_key))
        return row

    def get_curriculum(self, user_id: str, curriculum_id: str) -> dict:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM curricula WHERE id = %s AND user_id = %s", (curriculum_id, user_id))
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Curriculum not found")
        return row


career_store = CareerStore()


def curriculum_view(row: dict) -> dict:
    """저장된 커리큘럼 행 → API 응답. 저장본이 계약과 다르면 409.

    단계의 학습자료(steps[].resource)는 GET /learning-resources와 같은 형태로 반입 내부 정보를 빼고 내보낸다.
    """
    try:
        result = CurriculumResult.model_validate(row["result"])
        request = CurriculumRequest.model_validate(row["request"])
    except ValidationError:
        raise HTTPException(status_code=409, detail="Stored curriculum integrity check failed") from None
    if str(result.job_id) != str(row["id"]) or str(result.analysis_id) != str(row["analysis_id"]):
        raise HTTPException(status_code=409, detail="Stored curriculum integrity check failed")
    return {"curriculum_id": str(row["id"]), "request": request.model_dump(mode="json"),
            "result": result.model_dump(mode="json", exclude=_CURRICULUM_INTERNAL_FIELDS),
            "created_at": row["created_at"].isoformat()}
