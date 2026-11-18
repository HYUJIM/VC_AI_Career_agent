"""포트폴리오 본문 작성기.

입력은 **실행 시점 재검증을 통과한 근거(Evidence)**뿐이다 (VC_ONLY). 사용자 자유 텍스트는 받지 않는다.
사용자 값은 머리말의 제목·이름·희망 직무 한 줄씩뿐이고, 줄을 나눌 수 없게 한 줄로 넣는다(_one_line).
배지 문구(성취 이름·발급기관·내용·성취 기준)도 발급기관이 서명했을 뿐 우리가 쓴 글이 아니므로 같은 방식으로 한 줄로 넣는다:
여러 줄 문구로 '## 검증된 자격증명' 같은 섹션이나 목록 항목을 본문에 끼워 넣지 못한다. 방향 제어·보이지 않는 서식 문자
(U+202E 등)는 지운다. 한 줄 안의 Markdown 기호는 사용자 값과 같이 그대로 둔다 (FE는 본문을 글자 그대로 보여 준다).
AI(LLM) 모드에서도 본문 틀은 이 작성기가 만든다: AI는 요약 문장과 자격증명별 관련성 한 줄만 쓰고(app.services.portfolio_llm),
그 문장은 "AI 작성" 표시와 함께 한 줄로 들어간다. 근거(이름·발급 기관·날짜·검증 결과)는 늘 재검증 결과에서 이 틀이 쓴다.
"""
from typing import Optional, Protocol

from app.core.text import strip_invisible


class PortfolioWriter(Protocol):
    name: str

    def write(self, *, holder_name: str, title: str, target_role: Optional[str],
              evidence: list[dict], ai_summary: Optional[str] = None,
              ai_highlights: Optional[dict[str, str]] = None) -> tuple[str, str]:
        """(content_markdown, bio_summary)"""
        ...


ROLE_PREFIX = "- 희망 직무: "


def _date(value: Optional[str]) -> str:
    return value[:10] if value else "-"


def _one_line(value: Optional[str]) -> Optional[str]:
    """본문 한 줄에 넣는 값(제목·이름·희망 직무, 배지 문구)의 줄 구분 문자(\\n, \\r, U+2028 등)를 공백으로 바꾸고
    방향 제어·보이지 않는 서식 문자(app.core.text.BIDI_AND_INVISIBLE)를 지운다.

    요청 검사(ensure_single_line) 전에 저장됐거나 DB에 직접 넣은 여러 줄 값, 발급기관이 서명한 여러 줄 배지 문구가 있어도
    본문 Markdown의 줄 구조를 바꾸지 못하게 한다. 한 줄 값은 그대로다. 문자열이 아닌 배지 값(숫자 등)은 예전처럼
    문자열로 바꿔 넣는다.
    """
    if not value:
        return value
    text = value if isinstance(value, str) else str(value)
    return " ".join(strip_invisible(text).splitlines())


# AI가 쓴 문장 앞에 붙이는 표시 (본문을 받은 사람이 사실 근거와 AI 문장을 구별하게).
AI_SUMMARY_NOTE = "(AI 작성: 아래 검증된 자격증명만 근거로 Claude가 쓴 소개입니다. 사실 확인은 검증 항목을 기준으로 하세요.)"
AI_HIGHLIGHT_PREFIX = "- 관련성(AI 작성): "


def ordered_evidence(evidence: list[dict]) -> list[dict]:
    """본문에 싣는 순서 (최근 발급부터). AI에 보내는 ref(c1, c2, ...)도 이 순서로 붙인다."""
    return sorted(evidence, key=lambda item: item["issued_at"] or "", reverse=True)


class TemplatePortfolioWriter:
    name = "template"

    def write(self, *, holder_name: str, title: str, target_role: Optional[str],
              evidence: list[dict], ai_summary: Optional[str] = None,
              ai_highlights: Optional[dict[str, str]] = None) -> tuple[str, str]:
        """본문은 늘 이 틀로 만든다. ai_summary·ai_highlights(record_id → 한 줄)를 주면 요약을 AI 문장으로 바꾸고 각
        자격증명 아래에 AI가 쓴 관련성 한 줄을 표시와 함께 붙인다. 근거(이름·발급 기관·날짜·검증)는 그대로 이 틀이 쓴다."""
        issuers = sorted({e["issuer_name"] for e in evidence})
        summary = (f"{len(issuers)}개 기관에서 발급한 {len(evidence)}개의 검증된 자격증명을 근거로 "
                   f"역량을 증명합니다.")
        lines = [f"# {_one_line(title)}", "", f"- 이름: {_one_line(holder_name)}"]
        if target_role:
            lines.append(f"{ROLE_PREFIX}{_one_line(target_role)}")
        if ai_summary:
            summary = _one_line(ai_summary)
            lines += ["", "## 요약", "", AI_SUMMARY_NOTE, "", summary, "", "## 검증된 자격증명", ""]
        else:
            lines += ["", "## 요약", "", summary, "", "## 검증된 자격증명", ""]
        highlights = ai_highlights or {}
        for e in ordered_evidence(evidence):
            # 배지 문구는 발급기관이 정한 값이다: 여러 줄이어도 한 줄로 넣는다 (가짜 섹션·목록 항목을 만들지 못하게).
            lines.append(f"### {_one_line(e['achievement_name'])}")
            lines.append(f"- 발급 기관: {_one_line(e['issuer_name'])}")
            lines.append(f"- 발급일: {_date(e['issued_at'])}" +
                         (f" / 만료일: {_date(e['expires_at'])}" if e.get("expires_at") else ""))
            if e.get("achievement_description"):
                lines.append(f"- 내용: {_one_line(e['achievement_description'])}")
            if e.get("criteria"):
                lines.append(f"- 성취 기준: {_one_line(e['criteria'])}")
            lines.append(f"- 검증: 서명·발급기관·폐기 여부 확인 완료 ({_date(e['verified_at'])} 기준)")
            if highlights.get(e["record_id"]):
                lines.append(f"{AI_HIGHLIGHT_PREFIX}{_one_line(highlights[e['record_id']])}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n", summary


def rewrite_header(content: str, *, title: str, target_role: Optional[str]) -> str:
    """제목·희망 직무가 수정되면 본문 머리(첫 `## ` 앞)의 해당 줄만 바꾼다.

    근거 섹션은 생성 시점에 재검증한 결과이므로 건드리지 않는다.
    """
    title, target_role = _one_line(title), _one_line(target_role)
    lines = content.split("\n")
    end = next((i for i, line in enumerate(lines) if line.startswith("## ")), len(lines))
    head = [line for line in lines[:end] if not line.startswith(ROLE_PREFIX)]
    title_idx = next((i for i, line in enumerate(head) if line.startswith("# ")), None)
    if title_idx is None:
        head[:0] = [f"# {title}", ""]
        title_idx = 0
    else:
        head[title_idx] = f"# {title}"
    if target_role:
        anchor = next((i for i, line in enumerate(head) if line.startswith("- 이름: ")), None)
        if anchor is None:
            # 이름 줄이 없으면 제목 바로 아래 목록으로 넣는다.
            head[title_idx + 1:title_idx + 1] = ["", f"{ROLE_PREFIX}{target_role}"]
        else:
            head.insert(anchor + 1, f"{ROLE_PREFIX}{target_role}")
    return "\n".join(head + lines[end:])


def get_portfolio_writer() -> PortfolioWriter:
    """FastAPI 의존성. AI 연동 시 여기서 AI 작성기를 돌려준다."""
    return TemplatePortfolioWriter()
