"""직무별 시장 통계: 기술 추출 규칙, 중복 제거, 통계 식, API."""

import itertools
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.career.canonical import digest_json
from src.career.contracts import CONTRACT_HEADER, CONTRACT_ID
from src.career.contracts.market import MarketPosting, MarketSnapshot, MarketSnapshotRequest
from src.career.skill_catalog import CATALOG as CAREER_CATALOG
from src.career.skill_catalog import bounded_normalized_text, normalized_text
from src.market import statistics
from src.market.catalog import CATALOG, ROLES, SKILL_BY_ID, STACK_EXACT, TEXT_PATTERNS
from src.market.statistics import (
    _text_skills,
    build_snapshot,
    clipped_normalized_text,
    extract_skills,
    wilson_interval,
)
from src.server.app import create_app

NOW = datetime(2026, 11, 14, 3, 0, tzinfo=timezone.utc)
SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "data" / "market_snapshots"


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


def _pattern_only_skills(normalized):
    """표기 사전 검사 없이 패턴만 돌린 결과 (사전 검사를 넣기 전의 _text_skills)."""
    return {
        skill_id
        for skill_id, patterns in TEXT_PATTERNS.items()
        if any(pattern.search(normalized) for _, pattern in patterns)
    }


def test_literal_precheck_finds_the_same_skills_as_the_patterns_alone():
    literals = [literal for patterns in TEXT_PATTERNS.values() for literal, _ in patterns if literal]
    assert len(literals) > 500
    texts = [
        "",
        normalized_text(posting().qualifications),
        "자바 스크립트, 자바 서버, apache spark와 apache 웹 서버, 개인정보 보호 관련 법령",
        " / ".join(literals),                               # 모든 표기가 단어로 나온다
        " ".join("x" + literal + "x" for literal in literals),   # 모든 표기가 다른 글자에 붙어 나온다
    ]
    texts += [f"{literal} 경험" for literal in literals] + [f"x{literal}" for literal in literals]
    for text in texts:
        assert _text_skills(text) == _pattern_only_skills(text)
    assert len(_text_skills(texts[3])) == len(TEXT_PATTERNS)  # 사전의 모든 기술
    assert _text_skills(texts[4]) == set()  # 표기는 있지만 단어 경계가 아니다: 패턴이 걸러 낸다


def test_patterns_run_only_when_their_literal_occurs(monkeypatch):
    searched = []

    class Spy:
        def __init__(self, pattern):
            self.pattern = pattern

        def search(self, text):
            searched.append(self.pattern.pattern)
            return self.pattern.search(text)

    monkeypatch.setattr(statistics, "TEXT_PATTERNS", {
        skill_id: [(literal, Spy(pattern)) for literal, pattern in patterns]
        for skill_id, patterns in TEXT_PATTERNS.items()
    })
    always = sum(1 for patterns in TEXT_PATTERNS.values() for literal, _ in patterns if not literal)
    assert always == 3  # 손으로 쓴 정규식(자바, apache, 개인정보 … 법령)은 표기 없이 항상 돈다
    assert _text_skills(normalized_text("관련 분야 경력 3년 이상, 원활한 커뮤니케이션")) == set()
    assert len(searched) == always  # 표기 사전 검사가 없으면 589개 패턴을 모두 돌린다
    searched.clear()
    assert _text_skills(normalized_text("Kotlin 서버 개발")) == {"kotlin"}
    assert len(searched) == always + 1


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


def test_importance_and_hash_do_not_depend_on_posting_order():
    # 가중 점수 합이 2.575, 가중치 합이 4라 중요도가 정확히 0.64375(4자리 반올림의 경계)인 입력이다.
    # 순서대로 더하면 공고 순서에 따라 0.6437과 0.6438이 번갈아 나왔다. math.fsum은 순서와 무관하다.
    places = [
        ("A사", {"tech_stack": ["Java"]}),
        ("B사", {"tech_stack": ["Java"]}),
        ("B사", {"title": "Java 개발자"}),
        ("B사", {"preferred": "Java 경험"}),
        ("B사", {"qualifications": "Java 필수"}),
    ]
    blank = {"tech_stack": ["zz"], "qualifications": "", "preferred": "", "responsibilities": ""}
    postings = [posting(str(i), company, **{"title": f"채용 {i}", **blank, **place})
                for i, (company, place) in enumerate(places)]
    results = set()
    for order in itertools.permutations(postings):
        snapshot = build_snapshot(MarketSnapshotRequest(role_id="backend_developer", as_of=NOW,
                                                        postings=list(order)), NOW).snapshot
        results.add((snapshot.skills[0].skill_id, snapshot.skills[0].importance, snapshot.content_hash))
    assert len(results) == 1 and {item[0] for item in results} == {"java"}


def test_weight_sum_does_not_depend_on_posting_order():
    # 위 입력의 공고 가중치(1과 0.75)는 어떤 순서로 더해도 정확히 4라서 가중치 합(weight_sum)은 고정하지 못한다.
    # 여기서는 가중치가 이진 소수로 떨어지지 않는다: 7건 회사는 건당 3/7, 5건 회사는 건당 0.6, 나머지 10곳은 1.
    # 합은 16이고 Java는 7건 회사의 기술스택 한 곳에만 있어 중요도가 (3/7 × 0.7) / 16 = 0.01875, 곧 4자리
    # 반올림의 경계다. 가중치를 순서대로 더하면 공고 순서에 따라 합이 16 또는 15.999999999999998이 되어
    # 0.0187과 0.0188이 섞여 나오고 content_hash도 갈린다. math.fsum은 항상 16이다.
    blank = {"tech_stack": ["zz"], "qualifications": "", "preferred": "", "responsibilities": ""}
    places = (
        [("A사", {"tech_stack": ["Java"]})] + [("A사", {})] * 6 + [("B사", {})] * 5
        + [(f"회사{i}", {}) for i in range(10)]
    )
    postings = [posting(str(i), company, **{"title": f"채용 {i}", **blank, **place})
                for i, (company, place) in enumerate(places)]
    shuffled = random.Random(0)
    results = set()
    for _ in range(300):
        shuffled.shuffle(postings)
        snapshot = build_snapshot(MarketSnapshotRequest(role_id="backend_developer", as_of=NOW,
                                                        postings=postings), NOW).snapshot
        assert snapshot.sample_size == 22 and snapshot.companies == 12
        results.add((snapshot.skills[0].skill_id, snapshot.skills[0].importance, snapshot.content_hash))
    assert len(results) == 1 and {item[:2] for item in results} == {("java", 0.0187)}


def test_clipped_text_equals_bounded_text_unless_it_expands_too_much():
    for text in ("㈜데이터컴퍼니 ＡＷＳ ① 우대", "Spring Boot\n  JPA", "…" * 64, ""):
        assert clipped_normalized_text(text) == bounded_normalized_text(text)
    # U+FDFA 한 글자는 NFKC로 18자가 된다: 거절하지 않고 2배 + 64자까지만 본다.
    assert clipped_normalized_text("\ufdfa" * 5) == normalized_text("\ufdfa" * 5)[:74]
    assert len(clipped_normalized_text("\ufdfa" * 1000)) == 2064


@pytest.mark.parametrize("change", [
    {"tech_stack": ["Java", "\ufdfa" * 5]},
    {"preferred": "\ufdfa" * 40},
    {"company": "\u3300" * 70},
    {"title": "\ufdfa" * 20},
    {"title": "\ufdfa" * 4, "responsibilities": "\ufdfa" * 4},  # 따로는 상한 안, 이어 붙이면 넘던 경우
    {"qualifications": "• Java 필수 " + "\ufdfa" * 200},
])
def test_one_posting_with_expanding_text_does_not_fail_the_whole_role(client, headers, change):
    # 공개 공고 한 건에 정규화로 크게 늘어나는 문자가 있어도(전에는 요청 전체가 422) 그 직무 통계는 나온다.
    hostile = posting(**{"external_id": "99", "company": "이상한 회사", "qualifications": "• Java 필수", **change})
    assert extract_skills(hostile)["java"] in {"required", "stack"}
    postings = [posting(str(i), f"회사{i}", title=f"백엔드 {i}") for i in range(3)] + [hostile]
    request = MarketSnapshotRequest(role_id="backend_developer", as_of=NOW, postings=postings)
    snapshot = build_snapshot(request, NOW).snapshot
    assert snapshot.sample_size == 4 and snapshot.duplicates_removed == 0
    assert {s.skill_id: s.mentioned_postings for s in snapshot.skills}["java"] == 4
    response = client.post("/v1/market/snapshots", headers=headers, json=request.model_dump(mode="json"))
    assert response.status_code == 200 and response.json()["snapshot"]["sample_size"] == 4


@pytest.mark.parametrize("place, level", [
    ("tech_stack", "stack"),
    ("qualifications", "required"),
    ("preferred", "preferred"),
    ("title", "mentioned"),
    ("responsibilities", "mentioned"),
])
def test_every_section_reaches_the_patterns_clipped(monkeypatch, place, level):
    # 자르는 함수만 고정하면 한 자리에서 normalized_text를 그대로 넘겨도 테스트가 모른다. 그러면 2만 자 구간이
    # 36만 자가 되어 패턴으로 가고, 그 구간의 계산량 상한이 사라진다. extract_skills가 패턴에 넘기는 다섯 자리를
    # 하나씩, 늘어나는 글(U+FDFA 60자 → 1,080자)을 넣어 확인한다.
    source = "Java " + "ﷺ" * 60
    limit = 2 * len(source) + 64
    assert len(normalized_text(source)) == 1085 and limit == 194
    seen = []
    real = statistics._text_skills
    monkeypatch.setattr(statistics, "_text_skills", lambda text: seen.append(text) or real(text))
    values = {"title": "채용", "tech_stack": ["zz"], "qualifications": "", "preferred": "",
              "responsibilities": ""}
    values[place] = [source] if place == "tech_stack" else source
    assert extract_skills(posting(**values)) == {"java": level}  # 잘린 앞부분에서는 그대로 찾는다
    assert len(seen) == 5  # 기술스택 항목 하나 + 자격요건 + 우대사항 + 제목 + 주요업무
    assert sorted(seen, key=len)[-1] == normalized_text(source)[:limit]


def test_title_and_responsibilities_are_searched_separately():
    blank = {"tech_stack": [], "qualifications": "", "preferred": ""}
    # 제목 끝 "Spring"과 주요업무 첫 단어 "Boot"가 이어져 Spring Boot로 세지 않는다.
    levels = extract_skills(posting(title="Backend Engineer - Spring", responsibilities="Boot camp 운영 지원",
                                    **blank))
    assert levels == {"spring": "mentioned"}
    # 둘 중 어느 쪽에 나와도 mentioned로 센다.
    levels = extract_skills(posting(title="Kafka 엔지니어", responsibilities="• Redis 운영", **blank))
    assert levels == {"kafka": "mentioned", "redis": "mentioned"}


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


def test_bundled_snapshots_still_load_and_match_their_hash():
    # 저장소에 넣어 둔 집계 결과(docs/GETTING_STARTED.md 7-1·BE/README의 import 단계가 읽는다)는 계약 모델이 extra='forbid'라서, 계약이나
    # 직렬화가 바뀌면 조용히 못 쓰게 된다. BE가 읽을 때와 같은 검사(형식 → content_hash 재계산)를 한다.
    paths = sorted(SNAPSHOT_DIR.glob("*.json"))
    assert paths, "data/market_snapshots에 스냅샷 파일이 없다"
    for path in paths:
        snapshot = MarketSnapshot.model_validate_json(path.read_bytes())
        payload = snapshot.model_dump(mode="json")
        stored = payload.pop("content_hash")
        assert stored == digest_json(payload), path.name


def test_every_career_alias_is_also_a_market_alias():
    # 두 사전은 같은 skill_id를 따로 관리한다(직무 비교 카탈로그는 버전이 고정돼 있어 넓히지 않는다).
    # 직무 비교가 알아보는 표기를 시장 통계가 놓치면 같은 문장을 서로 다르게 세므로, 포함 관계를 고정한다.
    for skill in CAREER_CATALOG.skills:
        market = {normalized_text(alias) for alias in SKILL_BY_ID[skill.skill_id].aliases}
        missing = {normalized_text(alias) for alias in skill.aliases} - market
        assert not missing, f"{skill.skill_id}: {sorted(missing)}"
