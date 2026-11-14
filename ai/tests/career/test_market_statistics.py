"""직무별 시장 통계: 기술 추출 규칙, 중복 제거, 통계 식, API."""

import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.career.canonical import digest_json
from src.career.contracts import CONTRACT_HEADER, CONTRACT_ID
from src.career.contracts.market import MarketPosting, MarketSnapshotRequest
from src.market.catalog import CATALOG, ROLES, STACK_EXACT
from src.market.statistics import build_snapshot, extract_skills, wilson_interval
from src.server.app import create_app

NOW = datetime(2026, 11, 14, 3, 0, tzinfo=timezone.utc)


def posting(external_id="1", company="가나다", title="백엔드 개발자", **overrides):
    values = {
        "source": "jumpit",
        "external_id": external_id,
        "source_url": f"https://jumpit.saramin.co.kr/position/{external_id}",
        "company": company,
        "title": title,
        "captured_at": NOW - timedelta(hours=1),
        "tech_stack": ["Java", "Spring Boot", "AWS"],
        "responsibilities": "• REST API 설계 및 개발",
        "qualifications": "• Java, Spring 기반 개발 경력 3년 이상\n• MySQL 사용 경험",
        "preferred": "• Kafka 사용 경험\n• Docker, Kubernetes 운영 경험",
    }
    values.update(overrides)
    return MarketPosting(**values)


@pytest.fixture
def client():
    with TestClient(create_app()) as current:
        yield current


@pytest.fixture
def headers():
    return {"Authorization": "Bearer " + os.environ["AI_SERVICE_TOKEN"], CONTRACT_HEADER: CONTRACT_ID}


def test_catalog_has_the_five_roles_and_unique_skill_ids():
    assert [role.role_id for role in ROLES] == [
        "bigdata_engineer", "privacy_specialist", "backend_developer", "frontend_developer", "infra_engineer",
    ]
    ids = [skill.skill_id for skill in CATALOG.skills]
    assert len(ids) == len(set(ids)) and len(ids) > 100
    # 기존 직무 비교 카탈로그와 겹치는 기술은 같은 ID를 쓴다.
    assert {"sql", "python", "scala", "spark", "kafka", "airflow", "etl", "data_modeling", "git", "linux",
            "aws", "docker"} <= set(ids)


def test_a_skill_takes_the_strongest_place_it_appears_in():
    levels = extract_skills(posting())
    assert levels["java"] == "required"        # 자격요건과 기술스택 모두 → 자격요건
    assert levels["spring"] == "required"
    assert levels["mysql"] == "required"
    assert levels["spring_boot"] == "stack"    # 기술스택에만
    assert levels["aws"] == "stack"
    assert levels["kafka"] == "preferred"
    assert levels["kubernetes"] == "preferred"
    assert levels["rest_api"] == "mentioned"   # 주요업무에만


@pytest.mark.parametrize("text, expected, unexpected", [
    ("JavaScript, TypeScript 능숙자", {"javascript", "typescript"}, {"java"}),
    ("자바스크립트 개발 경험", {"javascript"}, {"java"}),
    ("자바 기반 서버 개발", {"java"}, {"javascript"}),
    ("Apache Spark, Apache Kafka 운영", {"spark", "kafka"}, {"web_server"}),
    ("Nginx, Apache 웹 서버 운영", {"web_server"}, {"spark"}),
    ("React Native 앱 개발", {"mobile_cross", "react"}, set()),
    ("ISMS-P 인증 대응 및 개인정보 보호 관련 법령 이해", {"isms_p", "privacy_law"}, set()),
    ("개인정보 영향평가(PIA) 수행, CPPG 보유자", {"pia", "cert_cppg"}, set()),
    ("We go to market fast", set(), {"go"}),
    ("Golang 서버 개발", {"go"}, set()),
])
def test_alias_rules(text, expected, unexpected):
    levels = extract_skills(posting(tech_stack=[], responsibilities="", preferred="", qualifications=text,
                                    title="채용"))
    assert expected <= set(levels) and not (unexpected & set(levels))


def test_short_names_count_only_as_whole_tech_stack_items():
    assert STACK_EXACT["go"] == "go" and STACK_EXACT["r"] == "r_lang"
    levels = extract_skills(posting(tech_stack=["Go", "R", "C"], qualifications="", preferred="",
                                    responsibilities="", title="채용"))
    assert levels == {"go": "stack", "r_lang": "stack", "cpp": "stack"}


def test_statistics_counts_rates_and_importance():
    postings = [
        posting("1", "A사"),
        posting("2", "B사", tech_stack=["Python"], qualifications="• Python 개발 경험", preferred="",
                responsibilities=""),
        posting("3", "C사", tech_stack=[], qualifications="• Java 개발 경험", preferred="• Python 경험",
                responsibilities=""),
        posting("4", "D사", tech_stack=["Java"], qualifications="", preferred="", responsibilities="• 서버 개발"),
    ]
    out = build_snapshot(MarketSnapshotRequest(role_id="backend_developer", as_of=NOW, postings=postings), NOW)
    snapshot = out.snapshot
    by_id = {s.skill_id: s for s in snapshot.skills}
    assert snapshot.sample_size == 4 and snapshot.companies == 4 and snapshot.duplicates_removed == 0
    java = by_id["java"]
    assert (java.mentioned_postings, java.required_postings, java.stack_postings, java.preferred_postings) == (3, 2, 1, 0)
    assert java.mention_rate == 0.75 and java.required_ratio == round(2 / 3, 4)
    assert java.importance == round((1.0 + 1.0 + 0.7) / 4, 4)          # 필수 2건 + 기술스택 1건
    python = by_id["python"]
    assert python.importance == round((1.0 + 0.5) / 4, 4)               # 필수 1건 + 우대 1건
    low, high = wilson_interval(3, 4)
    assert (java.mention_rate_low, java.mention_rate_high) == (round(low, 4), round(high, 4))
    assert [s.rank for s in snapshot.skills] == list(range(1, len(snapshot.skills) + 1))
    assert [s.importance for s in snapshot.skills] == sorted((s.importance for s in snapshot.skills), reverse=True)
    assert "SMALL_SAMPLE_STATISTICS_ARE_INDICATIVE_ONLY" in snapshot.warnings and "SINGLE_SOURCE" in snapshot.warnings
    assert {e.external_id for e in out.extractions} == {"1", "2", "3", "4"}


def test_reposts_and_same_ids_are_counted_once():
    postings = [posting("1"), posting("1"), posting("2"), posting("3", title="백엔드 개발자 (신입)")]
    snapshot = build_snapshot(MarketSnapshotRequest(role_id="backend_developer", as_of=NOW, postings=postings),
                              NOW).snapshot
    assert snapshot.sample_size == 2 and snapshot.duplicates_removed == 2


def test_one_company_cannot_dominate_importance():
    many = [posting(str(i), "큰회사", title=f"백엔드 {i}", tech_stack=["PHP"], qualifications="• PHP 경험 필수",
                    preferred="", responsibilities="") for i in range(9)]
    other = [posting("x", "작은회사", tech_stack=["Java"], qualifications="• Java 경험 필수", preferred="",
                     responsibilities="")]
    snapshot = build_snapshot(MarketSnapshotRequest(role_id="backend_developer", as_of=NOW, postings=many + other),
                              NOW).snapshot
    by_id = {s.skill_id: s for s in snapshot.skills}
    assert by_id["php"].mention_rate == 0.9
    # 큰회사 9건의 가중 합은 3, 작은회사 1건은 1 → PHP 중요도 3/4, Java 1/4
    assert by_id["php"].importance == 0.75 and by_id["java"].importance == 0.25
    assert "ONE_COMPANY_EXCEEDS_20_PERCENT_OF_POSTINGS" in snapshot.warnings


def test_pairs_experience_and_hash():
    postings = []
    for i in range(10):
        years = [None, 0, 2, 5, 9][i % 5]
        both = i < 6
        postings.append(posting(
            str(i), f"회사{i}", title=f"백엔드 {i}", experience_min_years=years,
            tech_stack=["Docker", "Kubernetes"] if both else ["Redis"], qualifications="", preferred="",
            responsibilities="",
        ))
    request = MarketSnapshotRequest(role_id="infra_engineer", as_of=NOW, postings=postings)
    snapshot = build_snapshot(request, NOW).snapshot
    pair = next(p for p in snapshot.pairs if {p.skill_a, p.skill_b} == {"docker", "kubernetes"})
    assert pair.postings == 6 and pair.lift == round((6 / 10) / (0.6 * 0.6), 4)
    assert {e.bucket: e.postings for e in snapshot.experience} == {
        "entry": 2, "junior": 2, "mid": 2, "senior": 2, "unspecified": 2}
    payload = snapshot.model_dump(mode="json")
    stored = payload.pop("content_hash")
    assert stored == digest_json(payload)
    # 같은 입력은 순서를 바꿔도(중복 없음) 같은 통계가 나온다.
    again = build_snapshot(MarketSnapshotRequest(role_id="infra_engineer", as_of=NOW,
                                                 postings=list(reversed(postings))), NOW).snapshot
    assert again.content_hash == snapshot.content_hash


def test_api_catalog_and_snapshot(client, headers):
    catalog = client.get("/v1/market/catalog", headers=headers)
    assert catalog.status_code == 200 and len(catalog.json()["roles"]) == 5
    body = MarketSnapshotRequest(role_id="backend_developer", as_of=NOW, postings=[posting("1"), posting("2", "다른")])
    response = client.post("/v1/market/snapshots", headers=headers, json=body.model_dump(mode="json"))
    assert response.status_code == 200
    data = response.json()
    assert data["snapshot"]["role_label"] == "백엔드 개발자" and data["snapshot"]["sample_size"] == 2
    assert "qualifications" not in str(data)  # 원문은 응답에 없다


@pytest.mark.parametrize("change", [
    {"role_id": "designer"},
    {"postings": []},
])
def test_api_rejects_bad_requests_without_echoing_input(client, headers, change):
    body = MarketSnapshotRequest(role_id="backend_developer", as_of=NOW, postings=[posting("1")]).model_dump(mode="json")
    body.update(change)
    response = client.post("/v1/market/snapshots", headers=headers, json=body)
    assert response.status_code == 422 and "designer" not in response.text


def test_api_needs_the_service_token(client):
    assert client.get("/v1/market/catalog").status_code == 401
    assert client.post("/v1/market/snapshots", json={}).status_code == 401


def test_posting_must_be_https_and_have_content():
    with pytest.raises(ValueError):
        posting(source_url="http://jumpit.saramin.co.kr/position/1")
    with pytest.raises(ValueError):
        posting(tech_stack=[], responsibilities="", qualifications="", preferred="")
