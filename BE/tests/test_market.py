"""직무별 시장 통계: 출처 어댑터(점핏 페이지 파서·요청 규칙), 수집·집계 배치, 조회 API.

AI 서비스(../ai)를 같은 프로세스에 띄워 BE의 실제 클라이언트·형식 검사를 거친다. 공고 HTML은 가상 테스트 값이다
(실제 사이트를 부르지 않는다: httpx.MockTransport).
"""
import asyncio
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.ai_contract.market import MarketPosting
from app.integrations.job_sources import (
    JumpitSource,
    SourceBlocked,
    SourceError,
    jumpit_url,
    mask_contacts,
    parse_closing_date,
    parse_experience,
    parse_jumpit,
)
from app.jobs import market as job
from app.services.market_store import market_store, snapshot_hash

AI_DIR = Path(__file__).resolve().parents[2] / "ai"
NOW = datetime(2026, 11, 14, 3, 0, tzinfo=timezone.utc)


def page(title="백엔드 개발자", company="가상회사", stack=("Java", "Spring Boot", "AWS"),
         duties="• REST API 설계 및 개발", required="• Java 개발 경력 3년 이상\n• MySQL 사용 경험",
         preferred="• Kafka 사용 경험", career="경력 3~7년", closes="2026-11-25"):
    stack_html = "".join(f'<div><img alt="{s}"/>{s}</div>' for s in stack)
    return f"""<html><head><title>점핏</title><script>var x = "<dt>가짜</dt>";</script></head><body>
<a href="/company/abc?company_nm=x"><img alt="logo"/>{company}</a><h1>{title}</h1>
<div class="position_info">
<dl><dt>기술스택</dt><dd><pre>{stack_html}</pre></dd></dl>
<dl><dt>주요업무</dt><dd><pre>{duties}</pre></dd></dl>
<dl><dt>자격요건</dt><dd><pre>{required}</pre></dd></dl>
<dl><dt>우대사항</dt><dd><pre>{preferred}</pre></dd></dl>
<dl><dt>복지 및 혜택</dt><dd><pre>• 점심 제공, 문의 hr@example.test 02-123-4567</pre></dd></dl>
</div><dl><dt>경력</dt><dd>{career}</dd></dl><dl><dt>마감일</dt><dd>{closes}</dd></dl>
<dl><dt>근무지역</dt><dd>서울 어딘가</dd></dl></body></html>"""


# ── 출처 어댑터 ─────────────────────────────────────────────────

def test_parse_jumpit_page_keeps_only_the_sections_statistics_need():
    parsed = parse_jumpit(page(required="• Java 3년\n• 문의: who@example.test, 010-1234-5678"), "123", NOW)
    posting = MarketPosting(**parsed)
    assert (posting.company, posting.title) == ("가상회사", "백엔드 개발자")
    assert posting.tech_stack == ["Java", "Spring Boot", "AWS"]
    assert posting.responsibilities == "• REST API 설계 및 개발"
    assert posting.qualifications == "• Java 3년\n• 문의: [이메일], [전화번호]"
    assert (posting.experience_min_years, posting.experience_max_years) == (3, 7)
    assert posting.closes_at == datetime(2026, 11, 25, 14, 59, 59, tzinfo=timezone.utc)  # 한국 시간 그날 끝
    assert str(posting.source_url) == "https://jumpit.saramin.co.kr/position/123"
    dumped = json.dumps(parsed, default=str, ensure_ascii=False)
    assert "점심" not in dumped and "서울" not in dumped and "가짜" not in dumped  # 복지·주소·스크립트는 버린다


@pytest.mark.parametrize("text, expected", [
    ("경력 3~20년", (3, 20)), ("신입", (0, None)), ("신입~5년", (0, 5)), ("경력 5년 이상", (5, None)),
    ("경력 무관", (None, None)), ("", (None, None)), ("경력 9~2년", (None, None)),
])
def test_parse_experience(text, expected):
    assert parse_experience(text) == expected


def test_parse_closing_date_and_masking():
    assert parse_closing_date("상시") is None and parse_closing_date("2026-13-40") is None
    assert mask_contacts("a@b.co 02-123-4567 010 1234 5678") == "[이메일] [전화번호] [전화번호]"


@pytest.mark.parametrize("bad", ["", "12a", "../1", "1/../../x", "1?x=1", "１２３", "1" * 13])
def test_only_numeric_posting_ids_become_urls(bad):
    with pytest.raises(SourceError):
        jumpit_url(bad)


def test_unreadable_page_is_an_error():
    with pytest.raises(SourceError):
        parse_jumpit("<html><body><h1>없는 공고</h1></body></html>", "1", NOW)


def test_source_requests_one_fixed_host_slowly_and_stops_when_blocked():
    seen, sleeps, clock = [], [], [0.0]

    def handler(request: httpx.Request):
        seen.append(request)
        if request.url.path.endswith("/3"):
            return httpx.Response(429)
        if request.url.path.endswith("/4"):
            return httpx.Response(302, headers={"Location": "https://evil.example/x"})
        return httpx.Response(200, text=page())

    source = JumpitSource(delay_seconds=0.1, transport=httpx.MockTransport(handler),
                          sleep=lambda s: (sleeps.append(s), clock.__setitem__(0, clock[0] + s)),
                          clock=lambda: clock[0])
    assert source.delay == 1.0  # 최소 간격 아래로 못 내린다
    assert source.fetch("1")["external_id"] == "1" and source.fetch("2")["title"] == "백엔드 개발자"
    assert sleeps == [1.0]  # 첫 요청은 바로, 다음부터 간격을 둔다
    with pytest.raises(SourceBlocked):
        source.fetch("3")
    with pytest.raises(SourceError):
        source.fetch("4")  # 리다이렉트는 따라가지 않는다
    assert {r.url.host for r in seen} == {"jumpit.saramin.co.kr"} and all(r.url.scheme == "https" for r in seen)
    assert all("vc-career-research" in r.headers["user-agent"] for r in seen)
    source.close()


# ── 배치와 API ──────────────────────────────────────────────────

@pytest.fixture
def ai_app(monkeypatch):
    from app.core.config import get_settings
    from app.integrations import ai_service as module

    if str(AI_DIR) not in sys.path:
        sys.path.append(str(AI_DIR))
    monkeypatch.setenv("CAREER_AI_SKIP_DOTENV", "true")
    from src.server.app import create_app

    app = create_app()
    monkeypatch.setenv("AI_SERVICE_URL", "http://127.0.0.1:8100")
    monkeypatch.setenv("AI_SERVICE_TOKEN", secrets.token_urlsafe(40))
    monkeypatch.setattr(module.ai_service, "transport", httpx.ASGITransport(app=app))
    get_settings.cache_clear()
    yield app
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def clean_market_tables(db):
    def wipe():
        db.execute("TRUNCATE market_postings, market_snapshots")
    wipe()
    yield
    wipe()


class FakeSource:
    name, rights_basis = "jumpit", "research_authorization"

    def __init__(self, pages: dict):
        self.pages, self.fetched = pages, []

    def fetch(self, external_id):
        self.fetched.append(external_id)
        value = self.pages[external_id]
        if isinstance(value, Exception):
            raise value
        return parse_jumpit(value, external_id, NOW)


def test_collect_stores_postings_and_skips_unreadable_ones(db):
    source = FakeSource({"1": page(), "2": SourceError("없음"), "3": page(company="다른회사", stack=("Python",))})
    result = job.collect("backend_developer", ["1", "2", "3"], source=source)
    assert result == {"created": 2, "updated": 0, "unchanged": 0, "filtered": 0, "failed": 1, "blocked": False,
                      "reused": 0}
    # 같은 공고를 다른 직무로 다시 모으면 출처에 다시 요청하지 않고 직무만 덧붙인다.
    before = len(source.fetched)
    again = job.collect("infra_engineer", ["1"], source=source, now=lambda: NOW)
    assert (again["updated"], again["reused"]) == (1, 1) and len(source.fetched) == before
    # 재사용 시간을 0으로 주면 새로 읽고, 내용이 바뀌었으면 갱신한다.
    source.pages["3"] = page(company="다른회사", stack=("Python", "Django"))
    assert job.collect("backend_developer", ["1", "3"], source=source, reuse_hours=0)["updated"] == 1
    rows = db.execute("SELECT external_id, role_ids, rights_basis, sections FROM market_postings ORDER BY 1").fetchall()
    assert [(r["external_id"], r["role_ids"]) for r in rows] == [
        ("1", ["backend_developer", "infra_engineer"]), ("3", ["backend_developer"])]
    assert rows[0]["rights_basis"] == "research_authorization" and rows[1]["sections"]["tech_stack"] == ["Python", "Django"]
    assert len(market_store.postings("backend_developer")) == 2 and len(market_store.postings("infra_engineer")) == 1


def test_collect_stops_when_the_source_blocks(db):
    source = FakeSource({"1": page(), "2": SourceBlocked("HTTP 429"), "3": page(company="셋")})
    result = job.collect("backend_developer", ["1", "2", "3"], source=source)
    assert result["blocked"] is True and result["created"] == 1 and source.fetched == ["1", "2"]


def test_privacy_role_keeps_only_privacy_postings(db):
    source = FakeSource({
        "1": page(title="개인정보보호 담당자", stack=(), duties="• 개인정보 처리방침 관리", required="• CPPG"),
        "2": page(title="보안 엔지니어", stack=(), duties="• 방화벽 운영", required="• 리눅스"),
        "3": page(title="정보보안 담당자", stack=(), duties="• 개인정보 영향평가\n• 개인정보 수탁사 점검", required="• ISMS-P"),
    })
    result = job.collect("privacy_specialist", ["1", "2", "3"], source=source)
    assert (result["created"], result["filtered"]) == (2, 1)


def test_role_filters_drop_postings_tagged_with_the_wrong_role(db):
    source = FakeSource({
        "1": page(title="프론트엔드 개발자", stack=("React", "TypeScript")),
        "2": page(title="WEB시스템 개발/운영", stack=("Java", "JSP", "Oracle")),          # 프런트엔드 분류에 달린 Java 운영 공고
        "3": page(title="웹 개발자", stack=("JavaScript", "PHP")),
    })
    # 판별식이 없던 때 저장된 공고를 흉내 낸다: 2번을 프런트엔드로 넣어 둔다.
    market_store.upsert_posting("frontend_developer", MarketPosting(**source.fetch("2")), "research_authorization")
    market_store.upsert_posting("backend_developer", MarketPosting(**source.fetch("2")), "research_authorization")
    result = job.collect("frontend_developer", ["1", "2", "3"], source=source, now=lambda: NOW)
    assert (result["created"], result["filtered"]) == (2, 1)
    assert [p.external_id for p in market_store.postings("frontend_developer")] == ["1", "3"]
    assert [p.external_id for p in market_store.postings("backend_developer")] == ["2"]  # 다른 직무는 그대로
    # 남는 직무가 없으면 공고를 지운다.
    job.collect("frontend_developer", ["2"], source=FakeSource({"2": page(title="경영지원", stack=("Excel",))}),
                reuse_hours=0)
    market_store.remove_role("jumpit", "2", "backend_developer")
    assert db.execute("SELECT count(*) AS n FROM market_postings WHERE external_id = '2'").fetchone()["n"] == 0


@pytest.mark.parametrize("role, title, stack, expected", [
    ("backend_developer", "백엔드 개발자", (), True),
    ("backend_developer", "개발자", ("Spring Boot",), True),
    ("backend_developer", "UI 디자이너", ("Figma",), False),
    ("bigdata_engineer", "데이터 엔지니어", (), True),
    ("bigdata_engineer", "개발자", ("Airflow",), True),
    ("bigdata_engineer", "웹 퍼블리셔", ("HTML",), False),
    ("infra_engineer", "DevOps 엔지니어", (), True),
    ("infra_engineer", "개발자", ("Kubernetes",), True),
    ("infra_engineer", "정보보안 담당자", ("ISMS",), False),
    ("frontend_developer", "Java 개발자", ("Java", "JSTL"), False),
])
def test_role_filter_rules(role, title, stack, expected):
    assert job.ROLE_FILTERS[role](MarketPosting(**parse_jumpit(page(title=title, stack=stack), "9", NOW))) is expected


def test_parse_ids():
    assert job.parse_ids("1, 2\n3,,2\n") == ["1", "2", "3"]
    with pytest.raises(ValueError):
        job.parse_ids("1,abc")


def _seed(n=3):
    pages = {str(i): page(company=f"회사{i}", stack=("Java", "Spring Boot") if i % 2 else ("Python", "Django"),
                          required="• Java 경험" if i % 2 else "• Python 경험") for i in range(1, n + 1)}
    job.collect("backend_developer", list(pages), source=FakeSource(pages))


def test_snapshot_goes_through_the_ai_service_and_api_serves_it(client, register, ai_app, db):
    _seed(5)
    report = asyncio.run(job.build_snapshots(["backend_developer", "frontend_developer"], now=lambda: NOW))
    assert report["frontend_developer"] == {"skipped": "no postings"}
    assert report["backend_developer"]["sample_size"] == 5 and report["backend_developer"]["saved"] is True
    # 같은 공고·같은 시각으로 다시 계산하면 같은 통계라 새로 쌓지 않는다.
    assert asyncio.run(job.build_snapshots(["backend_developer"], now=lambda: NOW))["backend_developer"]["saved"] is False

    user = register()
    listing = client.get("/api/v1/market/snapshots", headers=user["headers"])
    assert listing.status_code == 200
    [summary] = listing.json()["data"]
    assert (summary["role_id"], summary["role_label"], summary["sample_size"]) == ("backend_developer", "백엔드 개발자", 5)
    assert summary["top_skills"][0]["skill_id"] == "java" and len(summary["top_skills"]) <= 10

    detail = client.get("/api/v1/market/snapshots/backend_developer", headers=user["headers"])
    assert detail.status_code == 200
    data = detail.json()["data"]
    java = next(s for s in data["skills"] if s["skill_id"] == "java")
    assert java["mentioned_postings"] == 3 and java["required_postings"] == 3 and java["mention_rate"] == 0.6
    assert "SMALL_SAMPLE_STATISTICS_ARE_INDICATIVE_ONLY" in data["warnings"]
    text = detail.text
    assert "qualifications" not in text and "Java 경험" not in text and "research_authorization" not in text

    assert client.get("/api/v1/market/snapshots/frontend_developer", headers=user["headers"]).status_code == 404
    assert client.get("/api/v1/market/snapshots/designer", headers=user["headers"]).status_code == 422


def test_market_api_requires_login(client):
    assert client.get("/api/v1/market/snapshots").status_code == 401
    assert client.get("/api/v1/market/snapshots/backend_developer").status_code == 401


def test_tampered_snapshot_is_not_served(client, register, ai_app, db):
    _seed(3)
    asyncio.run(job.build_snapshots(["backend_developer"], now=lambda: NOW))
    db.execute("UPDATE market_snapshots SET snapshot = jsonb_set(snapshot, '{sample_size}', '999')")
    user = register()
    assert client.get("/api/v1/market/snapshots/backend_developer", headers=user["headers"]).status_code == 409
    assert client.get("/api/v1/market/snapshots", headers=user["headers"]).status_code == 409


def test_ai_answer_that_does_not_match_what_was_sent_is_not_saved(ai_app, db, monkeypatch):
    from app.integrations import ai_service as module

    _seed(3)
    original = module.ai_service.market_snapshot

    async def wrong_role(body):
        output = await original(body)
        return output.model_copy(update={"snapshot": output.snapshot.model_copy(update={"sample_size": 2})})

    monkeypatch.setattr(module.ai_service, "market_snapshot", wrong_role)
    with pytest.raises(RuntimeError):
        asyncio.run(job.build_snapshots(["backend_developer"], now=lambda: NOW))
    assert db.execute("SELECT count(*) AS n FROM market_snapshots").fetchone()["n"] == 0


def test_export_and_import_round_trip(ai_app, db, tmp_path):
    _seed(4)
    asyncio.run(job.build_snapshots(["backend_developer"], now=lambda: NOW))
    assert job.export_snapshots(tmp_path) == ["backend_developer.json"]
    exported = json.loads((tmp_path / "backend_developer.json").read_text(encoding="utf-8"))
    assert "qualifications" not in json.dumps(exported)  # 통계만, 원문 없음
    db.execute("TRUNCATE market_snapshots")
    assert job.import_snapshots(tmp_path) == {"backend_developer": True}
    restored = market_store.latest("backend_developer")
    assert restored.content_hash == exported["content_hash"] == snapshot_hash(restored)
    # 손댄 파일은 받지 않는다.
    exported["sample_size"] = 1
    (tmp_path / "backend_developer.json").write_text(json.dumps(exported), encoding="utf-8")
    with pytest.raises(ValueError):
        job.import_snapshots(tmp_path)


def test_cli_rejects_bad_ids_and_reports_failure(db, capsys):
    assert job.main(["collect", "--role", "backend_developer", "--ids", "1,abc"]) == 1
