"""포트폴리오 본문 작성기.

입력은 **실행 시점 재검증을 통과한 근거(Evidence)**뿐이다 (VC_ONLY). 사용자 자유 텍스트는 받지 않는다.
지금은 템플릿 작성기이고, AI 연동 시 같은 포트를 구현하는 작성기로 바꾼다.
"""
from typing import Optional, Protocol


class PortfolioWriter(Protocol):
    name: str

    def write(self, *, holder_name: str, title: str, target_role: Optional[str],
              evidence: list[dict]) -> tuple[str, str]:
        """(content_markdown, bio_summary)"""
        ...


ROLE_PREFIX = "- 희망 직무: "


def _date(value: Optional[str]) -> str:
    return value[:10] if value else "-"


class TemplatePortfolioWriter:
    name = "template"

    def write(self, *, holder_name: str, title: str, target_role: Optional[str],
              evidence: list[dict]) -> tuple[str, str]:
        issuers = sorted({e["issuer_name"] for e in evidence})
        summary = (f"{len(issuers)}개 기관에서 발급한 {len(evidence)}개의 검증된 자격증명을 근거로 "
                   f"역량을 증명합니다.")
        lines = [f"# {title}", "", f"- 이름: {holder_name}"]
        if target_role:
            lines.append(f"{ROLE_PREFIX}{target_role}")
        lines += ["", "## 요약", "", summary, "", "## 검증된 자격증명", ""]
        for e in sorted(evidence, key=lambda item: item["issued_at"] or "", reverse=True):
            lines.append(f"### {e['achievement_name']}")
            lines.append(f"- 발급 기관: {e['issuer_name']}")
            lines.append(f"- 발급일: {_date(e['issued_at'])}" +
                         (f" / 만료일: {_date(e['expires_at'])}" if e.get("expires_at") else ""))
            if e.get("achievement_description"):
                lines.append(f"- 내용: {e['achievement_description']}")
            if e.get("criteria"):
                lines.append(f"- 성취 기준: {e['criteria']}")
            lines.append(f"- 검증: 서명·발급기관·폐기 여부 확인 완료 ({_date(e['verified_at'])} 기준)")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n", summary


def rewrite_header(content: str, *, title: str, target_role: Optional[str]) -> str:
    """제목·희망 직무가 수정되면 본문 머리(첫 `## ` 앞)의 해당 줄만 바꾼다.

    근거 섹션은 생성 시점에 재검증한 결과이므로 건드리지 않는다.
    """
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
