"""직무별 시장 통계: 출처 어댑터(점핏 페이지 파서·요청 규칙), 수집·집계 배치, 조회 API.

AI 서비스(../ai)를 같은 프로세스에 띄워 BE의 실제 클라이언트·형식 검사를 거친다 (conftest의 ai_app). 공고 HTML은 가상
테스트 값이다 (실제 사이트를 부르지 않는다: httpx.MockTransport. 배치 진입점 시험도 출처를 가짜로 바꿔 끼운다).
- 출처 어댑터: 이메일·전화번호 가리기(구간·제목·회사·기술스택, 전각·+82·괄호·줄표·안심번호 표기, 연락처가 아닌 숫자는
  그대로), 긴 입력에도 길이에 비례하는 시간, 압축하지 않은 응답 요청, 한 건의 크기·전체 시간 상한 (gzip·deflate로 온
  응답은 상한 안에서만 푼다).
- 배치 진입점(python -m app.jobs.market): 아무 일도 못 했으면 종료 코드 1, 실패 이유는 traceback 없이 한 줄, LOG_LEVEL을 따름
  (모르는 LOG_LEVEL은 종료 코드 2).
- import: 없는·빈 폴더, 미래의 as_of, 한도를 넘는 이름, 범위 밖의 건수를 받지 않는다. 저장소의 통계 파일
  (ai/data/market_snapshots)은 받는다.
- 저장소: 검사에 실패한 행은 409 + 로그 한 줄(직무·행 ID), 이 빌드가 모르는 직무의 행은 목록에서 뺀다.
"""
import asyncio
import gzip
import json
import logging
import os
import subprocess
import sys
import time
import tracemalloc
import zlib
from datetime import datetime, timedelta, timezone

import httpx
import psycopg
import pytest

from app.ai_contract.market import MAX_SECTION_CHARS, MarketPosting, MarketSnapshot
from app.integrations.job_sources import (
    FETCH_DEADLINE_SECONDS,
    MAX_PAGE_BYTES,
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
from app.services.career_store import digest_json
from app.services.market_store import market_store, snapshot_hash
from tests.conftest import AI_DIR, BE_DIR

NOW = datetime(2026, 11, 14, 3, 0, tzinfo=timezone.utc)
SERVICE = {"X-Service-Key": "test-service-key"}


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


@pytest.mark.parametrize("phone", [
    "010-1234-5678", "010.1234.5678", "01012345678", "02-123-4567", "031 123 4567",       # 예전부터 가리던 표기
    "02)123-4567", "(02) 123-4567", "(010)1234-5678",                                     # 괄호 친 지역·휴대 번호
    "+82 10-1234-5678", "+82-10-1234-5678", "+821012345678", "+82 (0)10-1234-5678", "+82 2 123 4567",  # 국가번호
    "010 - 1234 - 5678", "010 . 1234 . 5678",                                             # 구분자 양옆 공백
    "０１０－１２３４－５６７８", "０２）１２３－４５６７",                                    # 전각 숫자·기호 (NFKC 뒤에 가린다)
    "0507-1234-5678", "0504-123-4567", "050712345678", "(0507) 1234-5678", "+82 507-1234-5678",  # 안심번호 (국번 4자리)
    "010\u20101234\u20105678", "010\u20111234\u20115678", "010\u20121234\u20125678",       # 하이픈·줄표 (U+2010~U+2015)
    "010\u20131234\u20135678", "010\u20141234\u20145678", "010\u20151234\u20155678",
    "010\u22121234\u22125678", "02\u2013123\u20134567",                                     # 빼기 기호(U+2212), en dash
    "010  1234  5678", "010\u00a01234\u00a05678", "010\t1234\t5678",                         # 공백 두 칸, NBSP, 탭
    "010\u200b1234\u200b5678", "010-12\u00ad34-5678", "0\u206010-1234-56\ufeff78",           # 보이지 않는 서식 문자(Cf)
    "02.123-4567", "010-1234.5678", "010 1234.5678", "010.1234 5678", "(02) 123.4567",    # 붙여 쓴 구분자는 섞여도 된다
    "010  1234.5678", "02.123  4567",                                                     # (공백 두 칸과 섞여도)
    "(02) 123 - 4567", "02 - 123-4567", "010 -1234- 5678", "010. 1234. 5678",             # 공백을 둔 구분자: 같은 것끼리
])
def test_phone_numbers_are_masked_in_the_formats_postings_use(phone):
    assert mask_contacts(f"문의: {phone} (평일)") == "문의: [전화번호] (평일)"


@pytest.mark.parametrize("email", ["hr@corp.co.kr", "a.b+c@d-e.co.kr", "hr＠corp.co.kr", "ｈｒ@ｃｏｒｐ.ｃｏ.ｋｒ",
                                   "hr\u200b@corp.co.kr", "h\u00adr@corp.co.kr", "hr@\u200ccorp.co\u2060.kr"])
def test_email_addresses_are_masked_including_fullwidth_spellings(email):
    # 뒤의 세 개: 주소 사이에 보이지 않는 서식 문자(폭 없는 공백·소프트 하이픈 등)를 끼워 넣은 표기
    assert mask_contacts(f"문의 {email} 로") == "문의 [이메일] 로"


@pytest.mark.parametrize("text", [
    # 회귀: 넓힌 전화번호 식이 연락처가 아닌 표기를 가렸다 (예전 식은 그대로 두던 것들).
    "03. 2025 2025 수상",                    # 줄 맨 앞의 목록 번호 '0N. ' + 연도 둘
    "03) 2263 7017 구간",                    # 줄 맨 앞의 목록 번호 '0N) ' + 숫자 둘
    "02) 2263 7017 구간", "  01) 3000 5000 만원 구간", "01. 2023. 2024 수상", "02) 2025 - 2026 로드맵",
    "Java 09\n- 2023 2020 로드맵",           # 줄 끝의 0N과 다음 줄을 이어 붙이지 않는다 (두 줄이 한 줄로 합쳐졌다)
    "버전 08\n2024-2025 운영", "항목 07\n2023 2024", "문의 010\n1234 5678", "지원 02-123\n4567",
    "기간 04.2025 - 2021",                   # MM.YYYY - YYYY: 점과 다른 구분자가 섞인 것은 날짜다
    "기간: 03.2020 - 2024", "재직 01.2019 - 12.2023", "근무 2024.03 - 2025.02", "06. 2023 2024 수상",
    "수상: 03. 2025 2025", "기간 04.2025 -2021", "기간 04 . 2025 - 2021", "버전 02 - 2024 . 2025",  # 점과 공백 둔 구분자가 섞임
    "010   1234   5678",                     # 공백 세 칸은 한 번호로 보지 않는다
    # 회귀: 0으로 시작하는 네 자리를 모두 국번으로 받아(안심번호 050N을 잡으려고) 연락처가 아닌 번호를 가렸다.
    "과제 0678 2023 2024", "문서번호 0549-2027-5578", "교대 0830 0430 1200", "(0678) 2023 2024",
])
def test_numbers_that_are_not_contacts_are_left_alone(text):
    assert mask_contacts(text) == text


def test_list_marker_at_line_start_is_skipped_but_a_phone_number_after_it_is_still_masked():
    # 목록 번호 뒤에 오는 진짜 번호는 가린다 (예전에는 '01) 010-1234'까지를 한 번호로 보고 뒤의 '-5678'을 남겼다).
    assert mask_contacts("01) 010-1234-5678\n02. 02-123-4567 (대표)") == "01) [전화번호]\n02. [전화번호] (대표)"
    # 줄 맨 앞이 아니면 지금처럼 지역번호로 본다. 줄 맨 앞이라도 '02) 123-4567'(서울 지역번호 + -로 이은 번호)은 전화번호다.
    assert mask_contacts("문의 02) 2263 7017") == "문의 [전화번호]"
    assert mask_contacts("02) 123-4567\n02)123-4567\n(02) 123-4567") == "[전화번호]\n[전화번호]\n[전화번호]"
    assert mask_contacts("03) 123-4567") == "03) 123-4567"  # 두 자리 지역번호는 02뿐이다: 목록 번호로 둔다


def test_masking_normalises_dashes_and_drops_invisible_format_characters():
    # 가리기 전에 고른 글이 그대로 저장된다: 줄표는 -로, 보이지 않는 서식 문자(Cf)는 빠진다. 다른 글자는 그대로다.
    assert mask_contacts("경력 3\u20135년, 2023\u20142024 프로젝트 \u2212 데이터\u200b엔지니어") == (
        "경력 3-5년, 2023-2024 프로젝트 - 데이터엔지니어")
    assert mask_contacts("• REST API 설계 · 운영 (Java/Kotlin) ~ 5년 → 리드") == "• REST API 설계 · 운영 (Java/Kotlin) ~ 5년 → 리드"


def test_masking_keeps_ordinary_numbers_and_long_local_parts_are_still_masked():
    for text in ("마감 2026-11-25", "2026.11.25 시작", "경력 3~20년", "Java 17, Spring Boot 3.2", "매출 1,234,567,890원",
                 "v0.12.345.6789", "포트 8080-8090", "문의(대표번호 없음)"):
        assert mask_contacts(text) == text
    # 괄호 안의 번호: 번호만 가리고 괄호는 둔다
    assert mask_contacts("문의(010-1234-5678)") == "문의([전화번호])"
    # 로컬 파트가 64자를 넘는 주소도 '@'와 도메인은 남지 않는다 (앞쪽 글자 몇 개만 남는다)
    masked = mask_contacts("x" * 70 + "@corp.example.co.kr")
    assert masked == "x" * 6 + "[이메일]"


def test_contacts_are_masked_in_title_company_and_stack_items_too():
    parsed = parse_jumpit(page(title="백엔드 010-1234-5678 a@b.co", company="회사 hr@corp.co.kr",
                               stack=("Java", "a@b.co 010-1234-5678", "Ｊａｖａ", "Java", "Spring"),
                               duties="문의 ｈｒ＠corp.co.kr +82 10-1234-5678"), "7", NOW)
    assert parsed["title"] == "백엔드 [전화번호] [이메일]"
    assert parsed["company"] == "회사 [이메일]"
    # 전각 'Ｊａｖａ'는 NFKC로 'Java'가 되어 같은 항목으로 합쳐진다
    assert parsed["tech_stack"] == ["Java", "[이메일] [전화번호]", "Spring"]
    assert parsed["responsibilities"] == "문의 [이메일] [전화번호]"
    dumped = json.dumps(parsed, default=str, ensure_ascii=False)
    assert "@" not in dumped and "1234" not in dumped
    MarketPosting(**parsed)  # 가린 값도 계약 형식을 통과한다


def test_stack_keeps_the_first_80_distinct_items_and_drops_overlong_ones():
    items = [f"기술{i}" for i in range(200)]
    parsed = parse_jumpit(page(stack=("Java", "x" * 81, "Java", *items)), "1", NOW)
    assert parsed["tech_stack"] == ["Java", *items[:79]]
    # NFKC로 80자를 넘게 되는 항목은 뺀다 (계약의 항목 길이 한도). '㈜' 한 글자는 '(주)' 세 글자가 된다.
    parsed = parse_jumpit(page(stack=("㈜" * 30, "㈜" * 20)), "1", NOW)
    assert parsed["tech_stack"] == ["(주)" * 20]


def test_long_section_is_cut_to_the_contract_limit_and_normal_text_is_unchanged():
    # 연락처가 없는 글은 예전과 같다: 한도 안이면 그대로, 넘으면 앞에서부터 한도까지.
    for length in (100, MAX_SECTION_CHARS, MAX_SECTION_CHARS + 1, MAX_SECTION_CHARS + 5000, 300_000):
        parsed = parse_jumpit(page(duties="가" * length), "1", NOW)
        assert parsed["responsibilities"] == "가" * min(length, MAX_SECTION_CHARS)
    MarketPosting(**parsed)


@pytest.mark.parametrize("contact", ["kim.recruit@corp.example.co.kr", "010-1234-5678", "+82 (0)10-1234-5678"])
def test_contact_straddling_a_cut_never_survives_in_part(contact):
    # 한도(20,000자)에 걸친 주소·번호는 통째로 가려진 뒤 잘린다. 가리기 전에 미리 자르는 자리(한도 + 여유분 400자)에 걸려
    # 반만 남은 것은 가려지지 않으므로 여유분과 함께 버려야 한다: 앞쪽에서 가려진 연락처가 많아 글이 400자 넘게 줄면
    # (earlier) 그 반쪽이 한도 안으로 들어온다.
    leaked = []
    for earlier in ("", "recruit.team@corp.example.co.kr " * 25):  # 뒤쪽은 가리면 31자 → 5자씩, 모두 650자가 준다
        for boundary in (MAX_SECTION_CHARS, MAX_SECTION_CHARS + 400):
            for before in range(0, len(contact) + 2):
                text = earlier + "가" * (boundary - before - len(earlier)) + contact + " 끝" + "나" * 2000
                cleaned = parse_jumpit(page(duties=text), "1", NOW)["responsibilities"]
                assert len(cleaned) <= MAX_SECTION_CHARS
                rest = cleaned.replace("[이메일]", "").replace("[전화번호]", "")
                if any(ch.isascii() and ch.isalnum() or ch in "@+()" for ch in rest):
                    leaked.append((bool(earlier), boundary, before, cleaned[-30:]))
    assert leaked == []


def test_hostile_page_text_is_cleaned_in_time_proportional_to_its_length():
    # 회귀: 이메일 식이 '@' 없이 이어진 영숫자 n자에 n²만큼 걸렸고(8만 자 3.5초 → 40만 자면 1분 넘게), 기술스택은 서로 다른
    # 항목 n개에 n²만큼 걸렸다(8만 개 12초 → 15만 개면 40초). 이제 모두 합쳐 1초가 안 걸리므로 한도는 넉넉하게 잡는다.
    def bare(duties="", stack="Java"):
        return ('<html><body><a href="/company/x">가상회사</a><h1>백엔드 개발자</h1><dl><dt>기술스택</dt><dd>' + stack
                + "</dd><dt>주요업무</dt><dd>" + duties + "</dd></dl></body></html>")

    started = time.perf_counter()
    for filler in ("a" * 400_000, "-" * 400_000, "0" * 400_000, "a@" * 200_000, "+82 (0" * 60_000):
        assert len(parse_jumpit(bare(duties=filler), "1", NOW)["responsibilities"]) == MAX_SECTION_CHARS
        assert mask_contacts(filler[:200_000]) == filler[:200_000]  # 자르지 않고 가리기만 해도 길이에 비례한다
    distinct = "<br>".join(format(i, "x") for i in range(150_000))
    assert parse_jumpit(bare(stack=distinct), "1", NOW)["tech_stack"] == [format(i, "x") for i in range(80)]
    assert parse_jumpit(bare(stack="<br>".join(["java"] * 200_000)), "1", NOW)["tech_stack"] == ["java"]
    addresses = "<br>".join(f"user{i}@corp.co.kr" for i in range(60_000))  # 가리면 모두 같은 항목이 된다
    assert parse_jumpit(bare(stack=addresses), "1", NOW)["tech_stack"] == ["[이메일]"]
    # 결합 문자만 이어진 글: NFKC(결합 문자 정렬이 n²)는 한도 + 여유분까지만 본다
    assert len(parse_jumpit(bare(duties="\u0301\u0323" * 200_000), "1", NOW)["responsibilities"]) <= MAX_SECTION_CHARS
    assert time.perf_counter() - started < 10


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
    # 압축하지 않은 응답을 청한다 (압축된 응답은 풀고 나서야 크기 상한을 볼 수 있다)
    assert all(r.headers["accept-encoding"] == "identity" for r in seen)
    source.close()


def test_source_gives_up_on_a_page_that_trickles_in_and_reads_the_next_one():
    # 읽기 한 번의 시간 제한(15초)에는 걸리지 않게 조금씩 오는 응답: 한 건 전체 시간(30초)을 넘기면 그만 읽는다.
    clock, served = [0.0], []

    def drip():
        body = page().encode()
        for start in range(0, len(body), 40):
            clock[0] += 10.0  # 40바이트마다 10초
            served.append(start)
            yield body[start:start + 40]

    def handler(request: httpx.Request):
        slow = request.url.path.endswith("/1")
        return httpx.Response(200, content=drip()) if slow else httpx.Response(200, text=page())

    source = JumpitSource(transport=httpx.MockTransport(handler), clock=lambda: clock[0],
                          sleep=lambda s: clock.__setitem__(0, clock[0] + s))
    with pytest.raises(SourceError, match="너무 느립니다") as slow:
        source.fetch("1")
    assert not isinstance(slow.value, SourceBlocked)  # 그 공고만 건너뛰고 수집은 이어 간다
    assert FETCH_DEADLINE_SECONDS == 30 and len(served) == 4  # 10·20·30초까지는 읽고 40초째 조각에서 멈춘다
    assert len(page().encode()) > 40 * 20  # (끝까지 읽었다면 20조각이 넘는다)
    assert source.fetch("2")["title"] == "백엔드 개발자"
    source.close()


def test_source_stops_reading_an_oversized_page_and_reports_undecodable_ones():
    served = []

    def endless():
        for index in range(1000):  # 64 KiB씩 62 MiB까지 줄 수 있는 응답
            served.append(index)
            yield b"a" * 65536

    def handler(request: httpx.Request):
        if request.url.path.endswith("/1"):
            return httpx.Response(200, content=endless())
        if request.url.path.endswith("/2"):
            return httpx.Response(200, content=b"<" * (MAX_PAGE_BYTES + 1))
        if request.url.path.endswith("/3"):  # 압축됐다면서 압축이 아닌 본문
            return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=iter([b"not-gzip"]))
        return httpx.Response(200, content=bytes(range(256)) * 50)  # UTF-8이 아닌 본문: 읽을 구간이 없다

    source = JumpitSource(transport=httpx.MockTransport(handler), sleep=lambda s: None)
    with pytest.raises(SourceError, match="너무 큽니다"):
        source.fetch("1")
    assert len(served) == MAX_PAGE_BYTES // 65536 + 1  # 상한(1.5MB)을 넘긴 조각에서 멈춘다
    with pytest.raises(SourceError, match="너무 큽니다"):
        source.fetch("2")
    with pytest.raises(SourceError, match="압축을 풀지 못했습니다"):
        source.fetch("3")
    with pytest.raises(SourceError, match="형식을 읽지 못했습니다"):
        source.fetch("4")
    source.close()


def _pieces(data: bytes, size: int, served: list | None = None):
    for start in range(0, len(data), size):
        if served is not None:
            served.append(start)
        yield data[start:start + size]


def _raw_deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)  # zlib 머리말이 없는 deflate
    return compressor.compress(data) + compressor.flush()


def test_source_reads_a_compressed_page_within_the_cap_and_refuses_other_codings():
    """압축하지 않은 응답을 청해도 서버·CDN이 gzip·deflate로 보내면 상한 안에서 직접 풀어 읽는다. 그 밖의 압축 방식(br·zstd·
    겹친 압축·모르는 이름)은 본문을 읽지 않고 그 공고만 건너뛴다."""
    html = page().encode()
    answers = {
        "1": ("gzip", gzip.compress(html)), "2": (" X-GZip ", gzip.compress(html)), "3": ("deflate", zlib.compress(html)),
        "4": ("identity", html), "5": ("", html),
        "10": ("br", html), "11": ("zstd", html), "12": ("gzip, gzip", gzip.compress(gzip.compress(html))),
        "13": ("compress", html), "14": ("gzip, identity", gzip.compress(html)),
        "20": ("gzip", gzip.compress(html)[:-9]),      # 중간에 끊긴 압축 본문
        "21": ("deflate", _raw_deflate(html)),         # zlib 머리말이 없는 deflate는 받지 않는다 (DXWorks 어댑터와 같다)
        "22": ("gzip", html),                          # 압축됐다면서 압축이 아닌 본문
    }
    served = []

    def handler(request: httpx.Request):
        coding, body = answers[request.url.path.rsplit("/", 1)[1]]
        return httpx.Response(200, headers={"Content-Encoding": coding}, content=_pieces(body, 7, served))

    source = JumpitSource(transport=httpx.MockTransport(handler), sleep=lambda s: None)
    for external_id in ("1", "2", "3", "4", "5"):
        assert source.fetch(external_id)["title"] == "백엔드 개발자", external_id
    for external_id in ("10", "11", "12", "13", "14"):
        served.clear()
        with pytest.raises(SourceError, match="받지 않는 방식으로 압축") as refused:
            source.fetch(external_id)
        assert served == [] and not isinstance(refused.value, SourceBlocked)  # 본문은 읽지 않고, 수집은 이어 간다
    for external_id in ("20", "21", "22"):
        with pytest.raises(SourceError, match="압축을 풀지 못했습니다"):
            source.fetch(external_id)
    assert source.fetch("1")["title"] == "백엔드 개발자"
    source.close()


@pytest.mark.parametrize("piece", [1024, 64 * 1024])
@pytest.mark.parametrize("coding, wbits", [("gzip", zlib.MAX_WBITS | 16), ("deflate", zlib.MAX_WBITS)])
def test_source_refuses_a_compressed_bomb_at_the_cap_with_small_memory(coding, wbits, piece):
    """회귀: 크기 상한을 httpx가 풀어낸 바이트에 걸어, 260 KB짜리 gzip 응답(256 MiB로 풀린다)을 읽는 수집 프로세스의 메모리가
    수백 MiB 늘어난 뒤에야 '너무 큽니다'로 끝났다. 이제 전송된 그대로 조금씩 받아 상한(1.5 MB)까지만 푼다.

    조각 크기는 둘 다 본다: 1 KiB씩 흘려보낼 때와, 실제 전송 계층이 주는 큰 조각(64 KiB)일 때. 1 KiB 조각 하나는 1 MiB
    남짓으로만 풀려서, 한 번에 푸는 양을 묶지 않아도(CappedBody가 max_length 없이 풀어도) 메모리 한도 안에 들어왔다.
    64 KiB 조각 하나는 묶지 않으면 66 MiB로 풀린다."""
    compressor = zlib.compressobj(6, zlib.DEFLATED, wbits)
    block = b"<" * (1024 * 1024)
    bomb = b"".join(compressor.compress(block) for _ in range(256)) + compressor.flush()
    assert len(bomb) < MAX_PAGE_BYTES // 4  # 전송되는 크기는 상한보다 한참 작다
    served = []
    source = JumpitSource(sleep=lambda s: None, transport=httpx.MockTransport(lambda request: httpx.Response(
        200, headers={"Content-Encoding": coding}, content=_pieces(bomb, piece, served))))
    tracemalloc.start()
    try:
        with pytest.raises(SourceError, match="너무 큽니다"):
            source.fetch("1")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        source.close()
    assert peak < 8 * 1024 * 1024  # 풀어낸 본문(상한 1.5 MB)과 조각 몇 개뿐이다
    assert len(served) * piece < len(bomb)  # 상한을 넘긴 조각에서 멈췄다 (끝까지 받지 않았다)


def test_compressed_page_is_capped_on_the_wire_and_after_decoding(monkeypatch):
    # 풀어낸 크기뿐 아니라 전송된 크기도 상한까지다. 압축하지 않고 담기만 한 gzip(compresslevel=0)은 전송 크기가 더 크다.
    from app.integrations import job_sources

    html = page().encode()
    packed = gzip.compress(html, compresslevel=0)
    assert len(packed) > len(html)

    def fetch():
        source = JumpitSource(sleep=lambda s: None, transport=httpx.MockTransport(lambda request: httpx.Response(
            200, headers={"Content-Encoding": "gzip"}, content=_pieces(packed, 64))))
        try:
            return source.fetch("1")["title"]
        finally:
            source.close()

    monkeypatch.setattr(job_sources, "MAX_PAGE_BYTES", len(html))  # 풀어낸 크기는 상한 안이고 전송 크기만 넘는다
    with pytest.raises(SourceError, match="너무 큽니다"):
        fetch()
    monkeypatch.setattr(job_sources, "MAX_PAGE_BYTES", len(packed))  # 전송·해제 크기가 모두 상한 안이면 읽는다
    assert fetch() == "백엔드 개발자"
    monkeypatch.setattr(job_sources, "MAX_PAGE_BYTES", len(html) - 1)  # 풀어낸 크기가 상한을 1바이트 넘는다
    packed = gzip.compress(html)  # (잘 압축된 본문: 전송 크기는 상한 안이다)
    with pytest.raises(SourceError, match="너무 큽니다"):
        fetch()


# ── 배치와 API ──────────────────────────────────────────────────

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
    # 사용자 전용 API다: 읽기용 서비스 키로는 부를 수 없다
    assert client.get("/api/v1/market/snapshots", headers=SERVICE).status_code == 401
    assert client.get("/api/v1/market/snapshots/backend_developer", headers=SERVICE).status_code == 401


def _store_warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == "app.services.market_store" and r.levelno == logging.WARNING]


def test_tampered_snapshot_is_not_served(client, register, ai_app, db, caplog):
    _seed(3)
    asyncio.run(job.build_snapshots(["backend_developer"], now=lambda: NOW))
    db.execute("UPDATE market_snapshots SET snapshot = jsonb_set(snapshot, '{sample_size}', '999')")
    row_id = db.execute("SELECT id FROM market_snapshots").fetchone()["id"]
    user = register()
    with caplog.at_level(logging.WARNING, logger="app.services.market_store"):
        detail = client.get("/api/v1/market/snapshots/backend_developer", headers=user["headers"])
        listing = client.get("/api/v1/market/snapshots", headers=user["headers"])
    assert detail.status_code == 409 and detail.json() == {"detail": "Market snapshot integrity check failed"}
    assert listing.status_code == 409
    # 409는 서버 로그에 남지 않으므로 저장소가 어느 행인지 한 줄씩 남긴다 (저장된 내용은 싣지 않는다)
    line = f"Market snapshot integrity check failed: role=backend_developer row={row_id}"
    assert _store_warnings(caplog) == [line, line]


@pytest.mark.parametrize("path, value", [
    ("{sample_size}", "100000000000000000000"),  # RFC 8785가 다룰 수 없는 정수: 해시 계산이 ValueError (예전에는 500)
    ("{skills,0,importance}", "1e400"),          # float로 읽으면 inf
    ("{role_id}", '"designer"'),                 # 계약에 없는 직무
    ("{content_hash}", '"' + "0" * 64 + '"'),    # 행의 해시 열과 다른 값
])
def test_stored_snapshot_that_cannot_be_checked_is_409_not_500(client, register, ai_app, db, caplog, path, value):
    _seed(3)
    asyncio.run(job.build_snapshots(["backend_developer"], now=lambda: NOW))
    db.execute("UPDATE market_snapshots SET snapshot = jsonb_set(snapshot, %s::text[], %s::jsonb)", (path, value))
    user = register()
    with caplog.at_level(logging.WARNING):
        res = client.get("/api/v1/market/snapshots/backend_developer", headers=user["headers"])
    assert res.status_code == 409 and res.json() == {"detail": "Market snapshot integrity check failed"}
    assert len(_store_warnings(caplog)) == 1
    assert not [r for r in caplog.records if r.name == "vc-be" and r.getMessage() == "Unhandled error"]


def test_row_of_a_role_this_build_does_not_know_is_left_out_of_the_list_and_export(client, register, ai_app, db,
                                                                                tmp_path):
    # 다른 버전의 배치가 쓴(또는 직무 이름이 바뀌기 전의) 행: 그 자체로는 해시가 맞는다. 예전에는 이 행 하나로 목록이 409였다.
    _seed(3)
    asyncio.run(job.build_snapshots(["backend_developer"], now=lambda: NOW))
    stored = db.execute("SELECT snapshot FROM market_snapshots").fetchone()["snapshot"]
    other = dict(stored, role_id="security_engineer")
    other.pop("content_hash")
    other["content_hash"] = digest_json(other)
    db.execute("INSERT INTO market_snapshots (role_id, as_of, sample_size, snapshot, content_hash)"
               " VALUES ('security_engineer', now(), %s, %s::jsonb, %s)",
               (other["sample_size"], json.dumps(other), other["content_hash"]))
    user = register()
    listing = client.get("/api/v1/market/snapshots", headers=user["headers"])
    assert listing.status_code == 200 and [item["role_id"] for item in listing.json()["data"]] == ["backend_developer"]
    assert job.export_snapshots(tmp_path) == ["backend_developer.json"]


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


def _rewrite(path, mutate) -> None:
    """통계 파일을 고친 뒤 해시를 다시 계산해 넣는다 (파일을 고친 사람이 할 수 있는 일: 해시는 열쇠 없는 SHA-256이다)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    data["content_hash"] = snapshot_hash(MarketSnapshot.model_validate(data))
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def exported(ai_app, db, tmp_path):
    """공고 4건으로 계산한 backend_developer 통계를 내보낸 파일 (DB의 통계는 비워 둔다)."""
    _seed(4)
    asyncio.run(job.build_snapshots(["backend_developer"], now=lambda: NOW))
    assert job.export_snapshots(tmp_path) == ["backend_developer.json"]
    db.execute("TRUNCATE market_snapshots")
    return tmp_path / "backend_developer.json"


def _snapshot_rows(db) -> int:
    return db.execute("SELECT count(*) AS n FROM market_snapshots").fetchone()["n"]


def test_import_refuses_a_directory_without_snapshot_files(db, tmp_path):
    (tmp_path / "empty").mkdir()
    (tmp_path / "notes.txt").write_text("통계 아님", encoding="utf-8")
    for directory in (tmp_path / "missing", tmp_path / "empty", tmp_path / "notes.txt", tmp_path):
        with pytest.raises(ValueError, match="통계 JSON 파일이 없습니다"):
            job.import_snapshots(directory)
    assert _snapshot_rows(db) == 0


def test_import_refuses_an_as_of_in_the_future(exported, db):
    # 최신 통계는 as_of가 가장 늦은 행이다: 먼 미래의 as_of가 들어가면 그 뒤에 계산한 통계가 영영 보이지 않는다.
    for as_of in ("9999-12-31T23:59:59Z", (NOW + timedelta(minutes=5, seconds=1)).isoformat()):
        _rewrite(exported, lambda data: data.update(as_of=as_of))
        with pytest.raises(ValueError, match="미래"):
            job.import_snapshots(exported.parent, now=lambda: NOW)
    assert _snapshot_rows(db) == 0
    # 서버 사이의 시계 차이(5분까지)는 받는다
    _rewrite(exported, lambda data: data.update(as_of=(NOW + timedelta(minutes=5)).isoformat()))
    assert job.import_snapshots(exported.parent, now=lambda: NOW) == {"backend_developer": True}
    # 기본 시계는 지금이다: 방금(NOW, 과거) 계산한 통계는 그대로 받는다
    _rewrite(exported, lambda data: data.update(as_of=NOW.isoformat()))
    assert job.import_snapshots(exported.parent) == {"backend_developer": True}


@pytest.mark.parametrize("mutate", [
    lambda data: data.update(role_label="가" * 81),
    lambda data: data.update(catalog_version="v" * 81),
    lambda data: data.update(extractor_version="r" * 81),
    lambda data: data["skills"][0].update(label="나" * 81),
    lambda data: data["skills"][-1].update(skill_id="s" * 81),
    lambda data: data["sources"][0].update(source="j" * 81),
    lambda data: data["pairs"].append({"skill_a": "a" * 81, "skill_b": "b", "postings": 1, "lift": 1.0}),
    lambda data: data.update(warnings=["다" * 201]),
    lambda data: data.update(warnings=["W"] * 21),
], ids=["role-label", "catalog-version", "extractor-version", "skill-label", "skill-id", "source", "pair",
        "warning-length", "warning-count"])
def test_import_refuses_oversized_labels_and_warnings(exported, db, mutate):
    _rewrite(exported, mutate)
    with pytest.raises(ValueError, match="한도를 넘는 통계 파일: backend_developer.json"):
        job.import_snapshots(exported.parent)
    assert _snapshot_rows(db) == 0


def test_import_accepts_labels_and_warnings_at_the_limit(exported, db):
    _rewrite(exported, lambda data: (data.update(role_label="가" * 80, warnings=["다" * 200] * 20),
                                     data["skills"][0].update(label="나" * 80)))
    assert job.import_snapshots(exported.parent) == {"backend_developer": True}
    assert market_store.latest("backend_developer").role_label == "가" * 80


def test_import_stores_nothing_when_one_file_is_refused(exported, db):
    # 파일을 모두 검사한 뒤에 저장한다: 이름순으로 뒤에 있는 파일이 틀려도 앞의 파일만 들어가는 일이 없다.
    bad = exported.with_name("z_frontend.json")
    bad.write_text(exported.read_text(encoding="utf-8"), encoding="utf-8")
    _rewrite(bad, lambda data: data.update(role_id="frontend_developer", as_of="9999-01-01T00:00:00Z"))
    with pytest.raises(ValueError, match="z_frontend.json"):
        job.import_snapshots(exported.parent)
    assert _snapshot_rows(db) == 0
    _rewrite(bad, lambda data: data.update(as_of=NOW.isoformat()))
    assert job.import_snapshots(exported.parent) == {"backend_developer": True, "frontend_developer": True}


COUNT_FIELDS = {
    "sample-size-negative": lambda data: data.update(sample_size=-5),
    "sample-size-over-int4": lambda data: data.update(sample_size=2**31),
    "sample-size-2**40": lambda data: data.update(sample_size=2**40),
    "duplicates-negative": lambda data: data.update(duplicates_removed=-1),
    "duplicates-over-int4": lambda data: data.update(duplicates_removed=2**31),
    "companies-negative": lambda data: data.update(companies=-1),
    "companies-over-int4": lambda data: data.update(companies=2**31),
    "skill-mentioned-negative": lambda data: data["skills"][0].update(mentioned_postings=-1),
    "skill-required-over-int4": lambda data: data["skills"][0].update(required_postings=2**31),
    "skill-stack-negative": lambda data: data["skills"][-1].update(stack_postings=-1),
    "skill-preferred-over-int4": lambda data: data["skills"][-1].update(preferred_postings=2**31),
    "pair-negative": lambda data: data["pairs"].append({"skill_a": "a", "skill_b": "b", "postings": -1, "lift": 1.0}),
    "pair-over-int4": lambda data: data["pairs"].append({"skill_a": "a", "skill_b": "b", "postings": 2**31, "lift": 1.0}),
}


@pytest.mark.parametrize("mutate", COUNT_FIELDS.values(), ids=COUNT_FIELDS.keys())
def test_import_refuses_counts_outside_the_database_range_and_stores_nothing(exported, db, mutate):
    """회귀: 건수는 범위를 보지 않아, 음수 표본 수(-5)인 파일이 그대로 저장됐고, INTEGER 범위를 넘는 표본 수(2**40)는 저장
    단계에서 DB가 'integer out of range'로 거절해 그 앞의 파일들만 들어간 채 끝났다 ('하나라도 받지 못하면 아무것도 넣지
    않는다'와 달랐다). 이제 검사 단계에서 거른다: 이름순으로 앞에 있는 정상 파일도 들어가지 않는다."""
    bad = exported.with_name("z_frontend.json")
    bad.write_text(exported.read_text(encoding="utf-8"), encoding="utf-8")
    _rewrite(bad, lambda data: (data.update(role_id="frontend_developer"), mutate(data)))
    with pytest.raises(ValueError, match="건수가 음수이거나 DB 정수 범위를 넘는 통계 파일: z_frontend.json"):
        job.import_snapshots(exported.parent)
    assert _snapshot_rows(db) == 0


def test_import_accepts_counts_at_the_database_limit(exported, db):
    assert job.MAX_IMPORT_COUNT == 2**31 - 1
    _rewrite(exported, lambda data: (
        data.update(sample_size=2**31 - 1, duplicates_removed=0, companies=2**31 - 1),
        data["skills"][0].update(mentioned_postings=2**31 - 1, required_postings=0),
        data["pairs"].append({"skill_a": "a", "skill_b": "b", "postings": 2**31 - 1, "lift": 1.0})))
    assert job.import_snapshots(exported.parent) == {"backend_developer": True}
    assert db.execute("SELECT sample_size FROM market_snapshots").fetchone()["sample_size"] == 2**31 - 1


def test_snapshot_files_shipped_in_the_repository_can_be_imported(db):
    # README의 "통계만 넣기" 경로: 저장소의 ai/data/market_snapshots가 지금 계약·해시·import 한도를 모두 통과해야 한다.
    shipped = AI_DIR / "data" / "market_snapshots"
    names = sorted(path.stem for path in shipped.glob("*.json"))
    assert names == sorted(job.ROLE_IDS)
    assert job.import_snapshots(shipped) == {role_id: True for role_id in job.ROLE_IDS}
    assert sorted(s.role_id for s in market_store.latest_all()) == names


# ── 배치 진입점 (python -m app.jobs.market): 종료 코드와 실패 한 줄 ──────────

def run_cli(caplog, *argv) -> tuple[int, list[str]]:
    """배치 진입점을 그대로 부른다. (종료 코드, 배치 로거가 남긴 '수준:메시지' 줄들)"""
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.jobs.market"):
        code = job.main([str(arg) for arg in argv])
    return code, [f"{r.levelname}:{r.getMessage()}" for r in caplog.records if r.name == "app.jobs.market"]


@pytest.fixture
def fake_site(monkeypatch):
    """배치가 만드는 출처(SOURCES['jumpit'])를 MockTransport로 답하는 것으로 바꾼다. 답을 정하는 dict와 받은 요청 목록."""
    answers: dict[str, httpx.Response] = {}
    requests: list[str] = []

    def handler(request: httpx.Request):
        external_id = request.url.path.rsplit("/", 1)[1]
        requests.append(external_id)
        return answers.get(external_id, httpx.Response(404))

    monkeypatch.setitem(job.SOURCES, "jumpit", lambda delay_seconds: JumpitSource(
        delay_seconds=delay_seconds, transport=httpx.MockTransport(handler), sleep=lambda seconds: None))
    return answers, requests


def test_cli_rejects_bad_ids_and_reports_failure(db, caplog, fake_site):
    assert run_cli(caplog, "collect", "--role", "backend_developer", "--ids", "1,abc") == (
        1, ["ERROR:실패: 공고 번호가 아닙니다: abc"])
    assert fake_site[1] == []  # 출처에 아무것도 요청하지 않았다


def test_cli_collect_fails_when_nothing_could_be_read(db, caplog, fake_site):
    answers, requests = fake_site
    collect = ("collect", "--role", "backend_developer", "--reuse-hours", "0", "--ids")
    # 출처의 페이지 형식이 바뀌었다: 세 건 모두 읽지 못하면 실패다 (예전에는 종료 코드 0).
    answers.update({str(i): httpx.Response(200, text="<html><body>새 화면</body></html>") for i in (1, 2, 3)})
    code, lines = run_cli(caplog, *collect, "1,2,3")
    assert code == 1 and lines[-1] == "ERROR:실패: 공고를 한 건도 읽지 못했습니다 (3건 모두 실패)"
    assert sum(line.startswith("WARNING:건너뜀") for line in lines) == 3
    # 없는 공고뿐이어도 같다
    assert run_cli(caplog, *collect, "8,9")[0] == 1
    # 한 건이라도 읽었으면 성공이다 (실패한 건은 경고 줄로 남는다)
    answers["1"] = httpx.Response(200, text=page())
    code, lines = run_cli(caplog, *collect, "1,2")
    assert code == 0 and not [line for line in lines if line.startswith("ERROR")]
    assert lines[-1].startswith("INFO:수집 결과 backend_developer: {'created': 1,") and "'failed': 1" in lines[-1]
    # 출처가 막으면 지금처럼 멈추고 실패다
    answers["5"] = httpx.Response(429)
    code, lines = run_cli(caplog, *collect, "5,1")
    assert code == 1 and lines[0] == "ERROR:수집 중단 (1/2): 출처가 요청을 막았습니다 (HTTP 429)"
    assert requests[-1] == "5"  # 막힌 뒤에는 요청하지 않는다
    assert db.execute("SELECT count(*) AS n FROM market_postings").fetchone()["n"] == 1


def test_cli_snapshot_fails_when_no_role_produced_statistics(ai_app, db, caplog):
    # 공고가 하나도 없다: 예전에는 아무 줄도 없이 종료 코드 0이었다.
    assert run_cli(caplog, "snapshot") == (1, ["ERROR:실패: 통계를 만든 직무가 없습니다 (수집된 공고 없음)"])
    code, lines = run_cli(caplog, "snapshot", "--role", "privacy_specialist")
    assert code == 1 and lines == ["INFO:통계 privacy_specialist: {'skipped': 'no postings'}",
                                  "ERROR:실패: 통계를 만든 직무가 없습니다 (수집된 공고 없음)"]
    assert _snapshot_rows(db) == 0
    # 공고가 있는 직무만 골라 계산한다 (--role 생략)
    _seed(3)
    code, lines = run_cli(caplog, "snapshot")
    assert code == 0 and len(lines) == 1
    assert lines[0].startswith("INFO:통계 backend_developer: {'sample_size': 3, 'saved': True")
    assert _snapshot_rows(db) == 1
    # 공고가 없는 직무를 함께 주어도 하나라도 만들었으면 성공이다
    assert run_cli(caplog, "snapshot", "--role", "backend_developer", "--role", "privacy_specialist")[0] == 0


def test_cli_export_and_import_round_trip(ai_app, db, caplog, tmp_path):
    _seed(4)
    asyncio.run(job.build_snapshots(["backend_developer"], now=lambda: NOW))
    out = tmp_path / "out"
    assert run_cli(caplog, "export", "--out", out) == (0, ["INFO:내보낸 파일: ['backend_developer.json']"])
    db.execute("TRUNCATE market_snapshots")
    assert run_cli(caplog, "import", "--dir", out) == (0, ["INFO:가져온 통계: {'backend_developer': True}"])
    assert run_cli(caplog, "import", "--dir", out) == (0, ["INFO:가져온 통계: {'backend_developer': False}"])
    assert _snapshot_rows(db) == 1


def test_cli_import_of_a_missing_or_empty_directory_is_a_failure(db, caplog, tmp_path):
    # docs/GETTING_STARTED.md 7-1의 import 명령을 폴더를 복사하기 전에 돌린 경우: 예전에는 '가져온 통계: {}'와 종료 코드 0이었다.
    (tmp_path / "empty").mkdir()
    for directory in (tmp_path / "missing", tmp_path / "empty"):
        assert run_cli(caplog, "import", "--dir", directory) == (
            1, [f"ERROR:실패: 통계 JSON 파일이 없습니다: {directory}"])
    assert _snapshot_rows(db) == 0


def test_cli_reports_storage_and_argument_errors_in_one_line(exported, db, caplog, fake_site, monkeypatch):
    # 아래 실패는 모두 예전에는 traceback으로 끝났다 (HTTPException·psycopg.Error·OverflowError를 잡지 않았다).
    # 1) INTEGER 열에 들어가지 않는 표본 수: 검사 단계에서 한 줄로 거절한다 (예전에는 저장 단계의 DB 오류였다)
    original = exported.read_text(encoding="utf-8")
    _rewrite(exported, lambda data: data.update(sample_size=2 ** 40))
    code, lines = run_cli(caplog, "import", "--dir", exported.parent)
    assert code == 1 and lines == ["ERROR:실패: 건수가 음수이거나 DB 정수 범위를 넘는 통계 파일: backend_developer.json"]
    #    저장 단계의 DB 오류(psycopg.Error)도 traceback 없이 한 줄이다 (여러 줄 메시지는 한 줄로 잇는다)
    exported.write_text(original, encoding="utf-8")
    with monkeypatch.context() as patch:
        def refuse(snapshot):
            raise psycopg.errors.NumericValueOutOfRange("integer out of range\nCONTEXT:  test")

        patch.setattr(market_store, "save_snapshot", refuse)
        code, lines = run_cli(caplog, "import", "--dir", exported.parent)
    assert code == 1 and lines == ["ERROR:실패: integer out of range CONTEXT: test"]
    # 2) 형식이 틀린 파일: 여러 줄짜리 검사 오류도 한 줄로, 길이를 잘라 남긴다
    exported.write_text(json.dumps({"role_id": "backend_developer", "skills": [{"x" * 50: 1}] * 300}), encoding="utf-8")
    code, lines = run_cli(caplog, "import", "--dir", exported.parent)
    assert code == 1 and len(lines) == 1 and "\n" not in lines[0]
    assert lines[0].startswith("ERROR:실패: ") and len(lines[0]) <= len("ERROR:실패: ") + job.MAX_FAILURE_LINE_CHARS
    # 3) 너무 큰 --reuse-hours (timedelta OverflowError). 출처에는 요청하지 않는다.
    code, lines = run_cli(caplog, "collect", "--role", "backend_developer", "--ids", "1", "--reuse-hours", "1e308")
    assert code == 1 and len(lines) == 1 and lines[0].startswith("ERROR:실패: ") and fake_site[1] == []
    # 4) 저장된 통계가 검사에 실패: 저장소의 409를 한 줄로
    asyncio.run(job.build_snapshots(["backend_developer"], now=lambda: NOW))
    db.execute("UPDATE market_snapshots SET snapshot = jsonb_set(snapshot, '{sample_size}', '999')")
    assert run_cli(caplog, "export", "--out", exported.parent / "out") == (
        1, ["ERROR:실패: Market snapshot integrity check failed"])
    assert not (exported.parent / "out" / "backend_developer.json").exists()
    # 5) DB에 닿지 않음: 여러 줄짜리 접속 오류도 한 줄
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "1")
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "2")
    for argv in (("export", "--out", exported.parent / "out"), ("snapshot",)):
        code, lines = run_cli(caplog, *argv)
        assert code == 1 and len(lines) == 1 and "\n" not in lines[0], lines
        assert lines[0].startswith("ERROR:실패: connection failed")


# 배치 진입점을 새 프로세스에서 불러 로깅 설정을 본다 (basicConfig는 처리기가 없는 프로세스에서만 설정한다).
# DB에 닿기 전에 실패하는 명령(빈 폴더 import)과, 소켓 없는 가짜 전송으로 보낸 httpx 요청 하나를 쓴다.
_BATCH_LOG_PROBE = r"""
import json, logging, sys
import httpx
from app.jobs import market as job
code = job.main(["import", "--dir", sys.argv[1]])
logging.getLogger("app.jobs.market").info("info-line")
with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as http:
    http.get("https://batch-probe.invalid/position/1")
print(json.dumps([code] + [logging.getLogger(name).level for name in ("", "httpx", "httpcore")]))
"""


@pytest.mark.parametrize("log_level, root_level", [(None, logging.INFO), ("", logging.INFO), (" ", logging.INFO),
                                                   ("warning", logging.WARNING), ("DEBUG", logging.DEBUG)])
def test_cli_logging_follows_log_level_like_the_server(tmp_path, log_level, root_level):
    # 회귀: 배치는 LOG_LEVEL과 관계없이 INFO로 남겼고, httpx 요청 줄(출처 공고 주소·AI 서비스 호스트)도 그대로 나갔다.
    # 비워 둔 LOG_LEVEL(.env의 `LOG_LEVEL=`)은 기본값 INFO다.
    env = {name: value for name, value in os.environ.items() if name != "LOG_LEVEL"}
    if log_level is not None:
        env["LOG_LEVEL"] = log_level
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run([sys.executable, "-B", "-c", _BATCH_LOG_PROBE, str(tmp_path)], cwd=BE_DIR, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    code, root, httpx_level, httpcore_level = json.loads(result.stdout.strip().splitlines()[-1])
    assert code == 1 and root == root_level
    assert "Traceback" not in result.stderr
    assert f"ERROR:app.jobs.market:실패: 통계 JSON 파일이 없습니다: {tmp_path}" in result.stderr.splitlines()
    assert ("INFO:app.jobs.market:info-line" in result.stderr) == (root_level <= logging.INFO)
    if root_level == logging.DEBUG:
        assert (httpx_level, httpcore_level) == (logging.NOTSET, logging.NOTSET)
        assert "HTTP Request: GET https://batch-probe.invalid/position/1" in result.stderr
    else:
        assert (httpx_level, httpcore_level) == (logging.WARNING, logging.WARNING)
        assert "batch-probe.invalid" not in result.stderr


@pytest.mark.parametrize("log_level", ["verbose", "10", "ＩＮＦＯ", "INFO,DEBUG"])
def test_cli_with_an_unknown_log_level_ends_with_one_error_line_and_exit_code_2(tmp_path, log_level):
    # 회귀: logging이 모르는 LOG_LEVEL이면 배치가 "Unknown level" traceback으로 끝났다 (고치기 전의 배치는 LOG_LEVEL을 보지
    # 않아 그런 값으로도 돌았다). 이제 인자 오류처럼 한 줄을 남기고 종료 코드 2로 끝난다. 아무 명령도 실행하지 않는다.
    env = {**os.environ, "LOG_LEVEL": log_level, "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-B", "-m", "app.jobs.market", "import", "--dir", str(tmp_path)],
                            cwd=BE_DIR, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 2 and result.stdout == ""
    assert "Traceback" not in result.stderr and "Unknown level" not in result.stderr
    errors = [line for line in result.stderr.splitlines() if not line.startswith(("usage:", " "))]
    assert errors == ["python -m app.jobs.market: error: LOG_LEVEL은 DEBUG, INFO, WARNING, ERROR, CRITICAL 중 하나여야 합니다"
                      " (비우면 INFO)"]
    assert "통계 JSON 파일이 없습니다" not in result.stderr  # import는 시작하지 않았다


def test_cli_reads_dotenv_before_it_configures_logging(tmp_path, monkeypatch):
    # 로컬 실행용 BE/.env에 적은 LOG_LEVEL도 따른다: .env를 읽은 뒤에 로그 수준을 정한다 (예전에는 순서가 반대였다).
    configured = []
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.setattr(job, "load_dotenv", lambda: monkeypatch.setenv("LOG_LEVEL", "error"))
    monkeypatch.setattr(job.logging, "basicConfig", lambda **kwargs: configured.append(kwargs["level"]))
    assert job.main(["import", "--dir", str(tmp_path)]) == 1
    assert configured == ["ERROR"]


def test_cli_help_needs_no_settings_database_or_ai_service():
    # CI의 Docker 확인 단계와 같다: 운영 이미지(APP_ENV=production, 비밀값·DB 없음)에서 --help가 떠야 한다.
    env = {"PATH": os.environ.get("PATH", ""), "APP_ENV": "production", "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-B", "-m", "app.jobs.market", "--help"], cwd=BE_DIR, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    assert "collect" in result.stdout and "import" in result.stdout and result.stderr == ""
