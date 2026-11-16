"""채용 공고 출처 어댑터 (직무별 시장 통계용 수집).

지금은 점핏(jumpit.saramin.co.kr) 공고 상세 페이지 하나만 읽는다. 원칙:

- 정해 둔 호스트의 HTTPS 주소만 요청한다. 주소는 숫자 공고 번호로만 만들고 리다이렉트는 따라가지 않는다 (SSRF 방지).
- 공개 공고 상세 페이지(/position/<번호>)만 읽는다. 목록·검색 API는 부르지 않는다
  (공고 번호 목록은 사람이 공개 목록 화면에서 모아 넘긴다).
- 이 코드는 robots.txt를 읽거나 따져 보지 않는다. 위 주소 형태 하나를 어댑터를 만들 때 사람이 출처의 robots.txt와 대조해
  확인했을 뿐이므로, 수집을 돌리기 전에 사람이 다시 확인한다.
- 요청 사이를 띄우고(기본 1.5초), 403·429를 받으면 바로 멈춘다. 식별 가능한 User-Agent를 쓴다.
  압축하지 않은 응답을 청하고, 한 건을 읽는 전체 시간(30초)과 크기(1.5MB)에 상한을 둔다. 그래도 gzip·deflate로 온 응답은
  전송된 크기와 풀어낸 크기를 각각 그 상한까지만 받고, 그 밖의 압축 방식은 받지 않는다.
- 통계에 필요한 구간(기술스택·주요업무·자격요건·우대사항)과 회사·제목·경력·마감일만 남긴다.
  이메일·전화번호는 구간뿐 아니라 제목·회사·기술스택 항목에서도 가린다. 복지·지원 절차·주소는 저장하지 않는다.
- 공고 문장은 데이터로만 다룬다.
"""
import re
import time
import unicodedata
import zlib
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Callable, Optional

import httpx

from app.ai_contract.market import MAX_SECTION_CHARS
from app.integrations.capped_body import BodyTooLarge, CappedBody, UnsupportedEncoding

USER_AGENT = "vc-career-research/0.1 (university capstone research; https://github.com/HYUJIM/VC_AI_Career_agent)"
JUMPIT_HOST = "jumpit.saramin.co.kr"
MAX_PAGE_BYTES = 1_500_000
MIN_DELAY_SECONDS = 1.0
# 공고 한 건을 읽는 데 쓰는 전체 시간의 상한(초). httpx 시간 제한은 읽기 한 번마다 다시 재므로, 응답을 조금씩 흘려보내면
# 한 건이 수집을 끝없이 붙잡는다. 주입한 시계(clock)로 잰다.
FETCH_DEADLINE_SECONDS = 30.0
KST = timezone(timedelta(hours=9))

# 두 정규식 모두 반복마다 상한이 있어 입력 길이에 비례하는 시간만 쓴다. 예전 이메일 식([...]+@)은 '@' 없이 이어진 영숫자
# n자에 n²만큼 걸렸다 (8만 자 3.5초, 1.4MB 구간이면 십수 분).
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,}")
# 전화번호: 0으로 시작하는 지역·휴대·안심 번호(02, 010, 0507), (02))나 국가번호(+82 10, +82 (0)10) 뒤에 3~4자리와 4자리.
# - 0으로 시작하는 국번은 두세 자리(02, 031, 010, 070)이고, 네 자리는 안심번호 050N(0507, 0504 등)뿐이다. 0으로 시작하는
#   네 자리를 모두 받으면 연락처가 아닌 번호를 가린다 ('과제 0678 2023 2024', '문서번호 0549-2027-5578', '교대 0830 0430 1200').
# - 사이의 구분자는 없거나, - 또는 . 하나이거나, 공백 한두 칸이다 (010-1234-5678, 02.123-4567, 010  1234  5678).
#   공백은 빈칸·탭만이다: 번호는 두 줄에 걸치지 않는다 ('Java 09' 다음 줄의 '- 2023 2020'을 이어 붙이지 않는다).
# - 구분자 양옆에 공백을 하나씩 둔 표기도 받는다 (010 - 1234 - 5678, 010 . 1234 . 5678, (02) 123 - 4567). 다만 그때는 점과
#   다른 구분자를 섞지 않는다: 공백을 둔 -는 점이 없는 번호에만, 공백을 둔 점은 두 자리 모두 점일 때만이다. 섞인 것은
#   날짜·목록 표기다 ('04.2025 - 2021', '03. 2025 2025').
_BLANK = r"[ \t]"
_GAP_TIGHT = rf"(?:[-.]|{_BLANK}{{1,2}})?"
_GAP_HYPHEN = rf"(?:{_BLANK}?-{_BLANK}?|{_BLANK}{{1,2}})?"
_GAP_DOT = rf"{_BLANK}?\.{_BLANK}?"
_PHONE_NUMBER = (r"(?<!\d)(?:\+82[-. \t]{0,2}(?:\(0\)[ \t]?|0)?\d{1,3}|\((?:050\d|0\d{1,2})\)|(?:050\d|0\d{1,2})\)?)"
                 + "(?:"
                 + "|".join(gap + r"\d{3,4}" + gap for gap in (_GAP_TIGHT, _GAP_HYPHEN, _GAP_DOT)) + r")\d{4}(?!\d)")
# 줄 맨 앞의 '0N. '·'0N) '는 번호 목록의 번호이지 지역번호가 아니다 ('03) 2263 7017 구간', '03. 2025 2025 수상').
# 이 식이 먼저 맞아 그 번호를 그대로 지나가므로 거기서 전화번호가 시작되지 않는다 (그 뒤에 오는 번호는 따로 가린다).
# 서울 지역번호 바로 뒤에 -로 이은 번호가 오는 '02) 123-4567'만은 전화번호로 본다.
_LIST_MARKER = rf"^{_BLANK}*0\d[.)]{_BLANK}(?!(?<=02\){_BLANK})\d{{3,4}}-\d{{4}}(?!\d))"
_PHONE = re.compile(rf"({_LIST_MARKER})|{_PHONE_NUMBER}", re.MULTILINE)
# 가리기 전에 -로 바꾸는 줄표(U+2010~U+2015 하이픈·대시, U+2212 빼기 기호): 010–1234–5678처럼 쓴 번호도 잡는다.
_DASHES = dict.fromkeys([*range(0x2010, 0x2016), 0x2212], "-")
# 긴 글을 가리기 전에 한도 뒤로 더 남겨 두는 글자 수. 한도에 걸친 주소·번호가 통째로 가려지게 한다
# (이메일 식은 시작에서 64 + 1 + 255 + 1 + 2 = 323자 안에서 맞는다).
_MASK_MARGIN = 400
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
    """이메일·전화번호를 가린다. 가리기 전에 글을 고른다 (돌려주는 글도 그렇게 고른 글이다):
    전각 숫자·＠처럼 호환 문자로 쓴 것도 잡도록 NFKC로 바꾸고, 줄표(– — − 등)는 -로 바꾸고, 보이지 않는 서식 문자
    (유니코드 Cf: 폭 없는 공백, 소프트 하이픈 등)는 뺀다 (번호·주소 사이에 끼워 넣으면 식이 맞지 않는다)."""
    text = unicodedata.normalize("NFKC", text).translate(_DASHES)
    if not text.isascii():  # ASCII에는 Cf 문자가 없다
        text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return _PHONE.sub(lambda match: match.group(1) or "[전화번호]", _EMAIL.sub("[이메일]", text))


def _masked(text: str, limit: int) -> str:
    """연락처를 가리고 limit자로 자른다. 긴 글은 가리기 전에 limit + _MASK_MARGIN자까지만 남긴다.

    정규식과 NFKC가 페이지 전체(최대 1.5MB)를 훑지 않게 하려는 것이다. 먼저 자른 끝에 걸려 반만 남은 주소·번호는 가려지지
    않으므로 그 끝의 여유분을 버린 뒤 limit자로 자른다 (가린 표시는 가린 글자보다 짧아서 그 조각은 여유분 안에 있다).
    limit + _MASK_MARGIN자 이하인 글은 전체를 가린 뒤 자르던 예전 결과와 같다.
    """
    head = text[:limit + _MASK_MARGIN]
    masked = mask_contacts(head)
    if len(text) > len(head):
        masked = masked[:-_MASK_MARGIN]
    return masked[:limit]


def _clean(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.replace("\x00", "").splitlines()]
    return _masked("\n".join(line for line in lines if line), MAX_SECTION_CHARS)


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
    title = _masked(" ".join("".join(parser.title_parts).split()), 255)
    company = _masked(" ".join(parser.company.split()), 200)
    sections = {field: _clean(fields.get(name, "")) for name, field in _SECTION_FIELDS.items()}
    # 이미 본 항목은 집합으로 걸러 내고 80개가 차면 멈춘다 (목록을 매번 훑으면 서로 다른 항목 n개에 n²만큼 걸렸다).
    stack: list[str] = []
    seen: set[str] = set()
    for line in fields.get("기술스택", "").splitlines():
        raw = " ".join(line.split())
        if not raw or len(raw) > 80 or raw in seen:
            continue
        seen.add(raw)
        item = mask_contacts(raw)
        if item != raw:  # 가리거나 NFKC로 바뀐 항목: 바뀐 값으로 길이·중복을 다시 본다
            if not item.strip() or len(item) > 80 or item in seen:
                continue
            seen.add(item)
        stack.append(item)
        if len(stack) == 80:
            break
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
        "tech_stack": stack,
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
            # 압축하지 않은 응답을 청한다. 그래도 압축해 보낸 응답은 fetch가 상한 안에서만 푼다.
            trust_env=False, headers={"User-Agent": USER_AGENT, "Accept": "text/html", "Accept-Encoding": "identity"},
        )

    def close(self) -> None:
        self._client.close()

    def fetch(self, external_id: str, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict:
        url = jumpit_url(external_id)
        if self._last is not None:
            wait = self.delay - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = started = self._clock()
        try:
            with self._client.stream("GET", url) as response:
                if response.status_code in (403, 429):
                    raise SourceBlocked(f"출처가 요청을 막았습니다 (HTTP {response.status_code})")
                if response.status_code != 200:
                    raise SourceError(f"공고를 읽지 못했습니다 (HTTP {response.status_code})")
                # 압축을 청하지 않았어도(Accept-Encoding: identity) 서버·CDN이 gzip·deflate로 답하면 상한 안에서만 직접 푼다
                # (capped_body.py). httpx에 맡기면 조각 하나를 통째로 풀고 나서야 크기를 볼 수 있어, 수백 바이트짜리 응답이
                # 수백 MiB가 된다. 그 밖의 방식(br·zstd·겹친 압축 등)은 본문을 읽지 않고 그 공고를 건너뛴다.
                try:
                    body = CappedBody(response.headers.get("Content-Encoding", ""), MAX_PAGE_BYTES)
                except UnsupportedEncoding:
                    raise SourceError("공고 응답이 받지 않는 방식으로 압축되어 있습니다") from None
                try:
                    if response.is_stream_consumed:
                        # 전송 계층이 본문을 이미 다 읽어 둔 응답 (본문을 통째로 넣어 만든 httpx.Response: 테스트용
                        # MockTransport). httpx가 그때 이미 풀어 두었으므로 다시 풀지 않고 크기만 본다.
                        body = CappedBody("", MAX_PAGE_BYTES)
                        body.feed(response.content)
                    else:
                        for chunk in response.iter_raw():  # 전송된 그대로 (httpx가 풀지 않은 바이트)
                            body.feed(chunk)
                            if self._clock() - started > FETCH_DEADLINE_SECONDS:
                                raise SourceError("공고 응답이 너무 느립니다")
                    content = body.finish()
                except BodyTooLarge:
                    raise SourceError("공고 페이지가 너무 큽니다") from None
                except zlib.error:
                    raise SourceError("공고 응답의 압축을 풀지 못했습니다") from None
        except httpx.HTTPError as exc:
            raise SourceError(f"공고 요청 실패 ({type(exc).__name__})") from exc
        return parse_jumpit(content.decode("utf-8", "replace"), external_id, now())


SOURCES = {"jumpit": JumpitSource}
