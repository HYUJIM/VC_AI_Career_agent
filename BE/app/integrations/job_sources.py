"""채용 공고 출처 어댑터 (직무별 시장 통계용 수집).

지금은 점핏(jumpit.saramin.co.kr) 공고 상세 페이지 하나만 읽는다. 원칙:

- 정해 둔 호스트의 HTTPS 주소만 요청한다. 주소는 숫자 공고 번호로만 만들고 리다이렉트는 따라가지 않는다 (SSRF 방지).
- robots.txt가 막지 않는 공개 공고 페이지만 읽는다. 목록·검색 API는 부르지 않는다
  (공고 번호 목록은 사람이 공개 목록 화면에서 모아 넘긴다).
- 요청 사이를 띄우고(기본 1.5초), 403·429를 받으면 바로 멈춘다. 식별 가능한 User-Agent를 쓴다.
- 통계에 필요한 구간(기술스택·주요업무·자격요건·우대사항)과 회사·제목·경력·마감일만 남긴다.
  이메일·전화번호는 가린다. 복지·지원 절차·주소는 저장하지 않는다.
- 공고 문장은 데이터로만 다룬다.
"""
import re
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Callable, Optional

import httpx

USER_AGENT = "vc-career-research/0.1 (university capstone research; https://github.com/HYUJIM/VC_AI_Career_agent)"
JUMPIT_HOST = "jumpit.saramin.co.kr"
MAX_PAGE_BYTES = 1_500_000
MAX_SECTION_CHARS = 20_000  # ai_contract.market.MAX_SECTION_CHARS와 같다
MIN_DELAY_SECONDS = 1.0
KST = timezone(timedelta(hours=9))

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)")
_SECTION_FIELDS = {"주요업무": "responsibilities", "자격요건": "qualifications", "우대사항": "preferred"}


class SourceError(Exception):
    """공고 한 건을 읽지 못했다 (없는 공고, 형식이 달라짐 등). 다음 공고로 넘어간다."""


class SourceBlocked(SourceError):
    """출처가 요청을 막았다 (403·429). 수집을 멈춘다."""


class _DefinitionList(HTMLParser):
    """<dt>이름</dt><dd>내용</dd> 쌍, <h1> 제목, 회사 링크 글자를 모은다. 스크립트·스타일은 버린다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pairs: list[list[str]] = []
        self.title_parts: list[str] = []
        self.company = ""
        self._buffer: list[str] = []
        self._in = None  # 'dt' | 'dd' | None
        self._skip = 0
        self._in_h1 = False
        self._in_company = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "h1":
            self._in_h1 = True
        elif tag in ("dt", "dd"):
            self._in, self._buffer = tag, []
        elif tag == "a" and (attrs.get("href") or "").startswith("/company/") and not self.company:
            self._in_company = True
        elif tag == "br" and self._in:
            self._buffer.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "h1":
            self._in_h1 = False
        elif tag == "a":
            self._in_company = False
        elif tag == "dt" and self._in == "dt":
            self.pairs.append(["".join(self._buffer).strip(), ""])
            self._in = None
        elif tag == "dd" and self._in == "dd":
            if self.pairs and not self.pairs[-1][1]:
                self.pairs[-1][1] = "".join(self._buffer).strip()
            self._in = None
        elif tag in ("div", "li", "p") and self._in == "dd":
            self._buffer.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_h1:
            self.title_parts.append(data)
        if self._in_company and not self.company:
            self.company = data.strip()
        if self._in:
            self._buffer.append(data)


def mask_contacts(text: str) -> str:
    return _PHONE.sub("[전화번호]", _EMAIL.sub("[이메일]", text))


def _clean(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.replace("\x00", "").splitlines()]
    return mask_contacts("\n".join(line for line in lines if line))[:MAX_SECTION_CHARS]


def parse_experience(text: str) -> tuple[Optional[int], Optional[int]]:
    """'경력 3~20년' → (3, 20), '신입' → (0, None), '신입~5년' → (0, 5), '경력 5년 이상' → (5, None), 모르면 (None, None)."""
    text = text.strip()
    newcomer = "신입" in text
    both = re.search(r"(\d{1,2})\s*[~\-]\s*(\d{1,2})\s*년", text)
    if both:
        low, high = int(both.group(1)), int(both.group(2))
        return (0 if newcomer else low, high) if high >= low else (None, None)
    upper = re.search(r"[~\-]\s*(\d{1,2})\s*년", text)
    if newcomer:
        return 0, int(upper.group(1)) if upper else None
    single = re.search(r"(\d{1,2})\s*년", text)
    return (int(single.group(1)), None) if single else (None, None)


def parse_closing_date(text: str) -> Optional[datetime]:
    """'2026-11-25' → 그날 한국 시간 23:59:59. '상시' 등은 None."""
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if not match:
        return None
    try:
        day = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), 23, 59, 59, tzinfo=KST)
    except ValueError:
        return None
    return day.astimezone(timezone.utc)


def jumpit_url(external_id: str) -> str:
    if not re.fullmatch(r"[0-9]{1,12}", external_id):  # \d는 전각 숫자도 받는다
        raise SourceError("공고 번호는 숫자여야 합니다")
    return f"https://{JUMPIT_HOST}/position/{external_id}"


def parse_jumpit(html: str, external_id: str, captured_at: datetime) -> dict:
    """점핏 공고 상세 HTML → MarketPosting에 넣을 값 (role 정보 제외)."""
    parser = _DefinitionList()
    parser.feed(html)
    parser.close()
    fields = {name: value for name, value in parser.pairs}
    title = " ".join("".join(parser.title_parts).split())[:255]
    company = " ".join(parser.company.split())[:200]
    sections = {field: _clean(fields.get(name, "")) for name, field in _SECTION_FIELDS.items()}
    stack = []
    for item in fields.get("기술스택", "").splitlines():
        item = " ".join(item.split())
        if item and len(item) <= 80 and item not in stack:
            stack.append(item)
    if not title or not company or not (stack or any(sections.values())):
        raise SourceError("공고 형식을 읽지 못했습니다 (제목·회사·본문 구간 없음)")
    low, high = parse_experience(fields.get("경력", ""))
    return {
        "source": "jumpit",
        "external_id": external_id,
        "source_url": jumpit_url(external_id),
        "company": company,
        "title": title,
        "experience_min_years": low,
        "experience_max_years": high,
        "captured_at": captured_at,
        "closes_at": parse_closing_date(fields.get("마감일", "")),
        "tech_stack": stack[:80],
        **sections,
    }


class JumpitSource:
    """공고 번호 → 공개 상세 페이지 한 장. 요청 간격은 이 객체가 지킨다."""

    name = "jumpit"
    rights_basis = "research_authorization"

    def __init__(self, *, delay_seconds: float = 1.5, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.delay = max(MIN_DELAY_SECONDS, delay_seconds)
        self._sleep, self._clock, self._last = sleep, clock, None
        self._client = httpx.Client(
            transport=transport, timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=False,
            trust_env=False, headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
        )

    def close(self) -> None:
        self._client.close()

    def fetch(self, external_id: str, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict:
        url = jumpit_url(external_id)
        if self._last is not None:
            wait = self.delay - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()
        try:
            with self._client.stream("GET", url) as response:
                if response.status_code in (403, 429):
                    raise SourceBlocked(f"출처가 요청을 막았습니다 (HTTP {response.status_code})")
                if response.status_code != 200:
                    raise SourceError(f"공고를 읽지 못했습니다 (HTTP {response.status_code})")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > MAX_PAGE_BYTES:
                        raise SourceError("공고 페이지가 너무 큽니다")
        except httpx.HTTPError as exc:
            raise SourceError(f"공고 요청 실패 ({type(exc).__name__})") from exc
        return parse_jumpit(body.decode("utf-8", "replace"), external_id, now())


SOURCES = {"jumpit": JumpitSource}
