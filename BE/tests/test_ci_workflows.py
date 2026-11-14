"""GitHub Actions 워크플로(.github)의 공급망·운영 번들 검사 규칙 (SD15, SD6·SD18 CI 검사).

워크플로는 앱 테스트로 확인되지 않으므로 아래 규칙이 깨지면 이 테스트가 실패하게 한다.
- 워크플로 최상위 permissions는 contents: read만 둔다 (쓰기가 필요한 job은 그 job에만 따로 준다).
- 외부 액션(uses)은 40자리 커밋 SHA로 고정하고 버전은 줄 끝 주석(# vX.Y.Z)으로 적는다.
- actions/checkout은 persist-credentials: false로 GITHUB_TOKEN을 git 설정에 남기지 않는다.
- 원격 스크립트를 셸로 바로 실행하지 않는다 (curl ... | bash).
- secrets는 단계(steps) 안의 env/with에서만 쓴다 (job·워크플로 env에 두면 모든 단계가 본다).
- secrets를 받는 job의 pip 설치는 버전을 ==로 고정한다 (또는 --require-hashes).
- FE job은 npm run build 뒤에 운영 번들(dist) 검사(외부 스크립트, 개발 전용 안내)를 한다.
- .github/dependabot.yml이 github-actions 갱신을 맡는다.
YAML 라이브러리 없이 줄 단위로 본다 (이 저장소 워크플로의 블록 스타일 기준).
"""
import re
from pathlib import Path

import pytest

GITHUB_DIR = Path(__file__).resolve().parents[2] / ".github"
WORKFLOWS_DIR = GITHUB_DIR / "workflows"
WORKFLOWS = sorted(WORKFLOWS_DIR.glob("*.y*ml")) if WORKFLOWS_DIR.is_dir() else []

pytestmark = pytest.mark.skipif(not WORKFLOWS, reason=".github/workflows가 없음 (BE 폴더만 있는 환경)")

USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*['\"]?([^'\"\s#]+)['\"]?\s*(#.*)?$")
SHA_PINNED_RE = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
SECRETS_RE = re.compile(r"(?<![\w.-])secrets\.")
PIPE_TO_SHELL_RE = re.compile(
    r"\b(?:curl|wget)\b[^\n]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b|\b(?:ba|z)?sh\s+<\(\s*(?:curl|wget)\b"
)
PIP_INSTALL_RE = re.compile(r"\b(?:pip3?|pipx|uv\s+pip)\s+install\b")


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _structural(line: str) -> bool:
    return bool(line.strip()) and not line.lstrip().startswith("#")


def _ancestors(lines: list[str], idx: int) -> list[str]:
    """idx 줄을 감싸는 상위 줄(들여쓰기가 더 작은 줄)들, 가까운 것부터."""
    out, cur = [], _indent(lines[idx])
    for line in reversed(lines[:idx]):
        if _structural(line) and _indent(line) < cur:
            out.append(line.strip())
            cur = _indent(line)
    return out


def _block(lines: list[str], start: int) -> list[str]:
    """start 줄과 그 아래로 더 깊게 들여쓴 줄들 (start 줄이 여는 블록)."""
    out = [lines[start]]
    for line in lines[start + 1:]:
        if _structural(line) and _indent(line) <= _indent(lines[start]):
            break
        out.append(line)
    return out


def _step_start(lines: list[str], idx: int) -> int:
    """idx 줄이 속한 단계(steps 목록의 '- ' 항목)의 첫 줄."""
    for j in range(idx, -1, -1):
        if lines[j].lstrip().startswith("- ") and (j == idx or _indent(lines[j]) < _indent(lines[idx])):
            return j
    return idx


def _jobs(lines: list[str]) -> dict[str, list[str]]:
    start = next(i for i, line in enumerate(lines) if re.match(r"^jobs:\s*$", line))
    jobs, name = {}, None
    for line in lines[start + 1:]:
        if _structural(line) and _indent(line) == 0:
            break
        if _structural(line) and _indent(line) == 2:
            name = line.strip().rstrip(":")
            jobs[name] = []
        elif name:
            jobs[name].append(line)
    return jobs


def _steps(job_lines: list[str]) -> list[str]:
    """job 본문을 단계별 텍스트로 나눈다 (steps: 아래 '- ' 항목 하나가 한 단계)."""
    start = next(i for i, line in enumerate(job_lines) if line.strip() == "steps:")
    step_indent, steps = None, []
    for line in job_lines[start + 1:]:
        if not _structural(line):
            if steps:
                steps[-1] += "\n" + line
            continue
        if step_indent is None:
            step_indent = _indent(line)
        if _indent(line) < step_indent:
            break
        if _indent(line) == step_indent and line.lstrip().startswith("- "):
            steps.append(line)
        elif steps:
            steps[-1] += "\n" + line
    return steps


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_top_level_permissions_read_only(path):
    lines = _lines(path)
    start = next((i for i, line in enumerate(lines) if re.match(r"^permissions:", line)), None)
    assert start is not None, (
        f"{path.name}: 최상위 permissions가 없어 GITHUB_TOKEN이 저장소 기본 권한(쓰기일 수 있음)을 받는다"
    )
    nested = [line.strip() for line in _block(lines, start)[1:] if _structural(line)]
    body = " ".join([lines[start].split(":", 1)[1].strip()] + nested)
    assert re.fullmatch(r"\{?\s*contents:\s*read\s*\}?", body), (
        f"{path.name}: 최상위 permissions는 contents: read만 둔다 ({body!r})"
    )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_actions_pinned_to_commit_sha_with_version_comment(path):
    bad = []
    for n, line in enumerate(_lines(path), 1):
        m = USES_RE.match(line)
        if not m or m.group(1).startswith("./") or re.match(r"^docker://.+@sha256:[0-9a-f]{64}$", m.group(1)):
            continue
        if not SHA_PINNED_RE.match(m.group(1)) or not re.fullmatch(r"#\s*v\d+(\.\d+)*", (m.group(2) or "").strip()):
            bad.append(f"{path.name}:{n} {line.strip()}")
    assert not bad, f"액션은 40자리 커밋 SHA로 고정하고 버전 주석을 단다 (예: actions/checkout@<sha> # v7.0.1): {bad}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_checkout_does_not_persist_token(path):
    lines = _lines(path)
    bad = []
    for i, line in enumerate(lines):
        m = USES_RE.match(line)
        if not m or not m.group(1).startswith("actions/checkout@"):
            continue
        step = "\n".join(_block(lines, _step_start(lines, i)))
        if not re.search(r"^\s*persist-credentials:\s*['\"]?false['\"]?\s*(?:#.*)?$", step, re.MULTILINE):
            bad.append(f"{path.name}:{i + 1}")
    assert not bad, f"actions/checkout에 persist-credentials: false가 없다: {bad}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_remote_script_piped_to_shell(path):
    lines = _lines(path)
    bad = [f"{path.name}:{i + 1}" for i, line in enumerate(lines)
           if _structural(line) and PIPE_TO_SHELL_RE.search(line)]
    assert not bad, f"원격 설치 스크립트를 셸로 바로 실행하지 않는다 (버전을 고정해 설치): {bad}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_secrets_only_in_steps(path):
    lines = _lines(path)
    bad = [
        f"{path.name}:{i + 1}"
        for i, line in enumerate(lines)
        if _structural(line) and SECRETS_RE.search(line) and "steps:" not in _ancestors(lines, i)
    ]
    assert not bad, f"secrets는 필요한 단계의 env/with에만 둔다 (job·워크플로 env면 모든 단계가 본다): {bad}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_jobs_with_secrets_install_only_pinned_packages(path):
    bad = []
    for job, body in _jobs(_lines(path)).items():
        if not any(SECRETS_RE.search(line) for line in body if _structural(line)):
            continue
        for line in body:
            if not (_structural(line) and PIP_INSTALL_RE.search(line)) or "--require-hashes" in line:
                continue
            args = line.split("install", 1)[1]
            reqs = [a.strip("'\"") for a in args.split() if not a.startswith("-")]
            if not reqs or not all(re.fullmatch(r"[A-Za-z0-9._\-\[\],]+==[A-Za-z0-9.+!_-]+", r) for r in reqs):
                bad.append(f"{path.name} {job}: {line.strip()}")
    assert not bad, f"secrets를 받는 job은 버전을 ==로 고정한 패키지만 설치한다: {bad}"


def test_raw_secret_values_only_in_the_step_that_needs_them():
    """Strix 워크플로: 비밀값 자체는 Run Strix 단계 env에만, 확인 단계는 등록 여부(불리언)만 받는다."""
    path = WORKFLOWS_DIR / "main.yml"
    if not path.is_file():
        pytest.skip("main.yml 없음")
    raw_secret = re.compile(r"\$\{\{\s*secrets\.\w+\s*\}\}")
    for job, body in _jobs(_lines(path)).items():
        for step in _steps(body):
            name = re.search(r"name:\s*(.+)", step)
            name = name.group(1).strip() if name else step.splitlines()[0].strip()
            structural = "\n".join(line for line in step.splitlines() if _structural(line))
            if raw_secret.search(structural):
                assert name == "Run Strix", f"{job}/{name}: 비밀값을 받는 단계는 Run Strix 하나뿐이어야 한다"
            elif SECRETS_RE.search(structural):
                assert re.search(r"HAS_STRIX_SECRETS:\s*\$\{\{[^}]*!=\s*''[^}]*\}\}", structural), (
                    f"{job}/{name}: 비밀값 대신 등록 여부(!= '')만 넘긴다"
                )


def test_fe_job_checks_dist_after_build():
    path = WORKFLOWS_DIR / "tests.yml"
    if not path.is_file():
        pytest.skip("tests.yml 없음")
    steps = _steps(_jobs(_lines(path))["fe"])
    build = next(i for i, s in enumerate(steps) if re.search(r"run:\s*npm run build\s*$", s, re.MULTILINE))
    # 주석·안내 문구가 아니라 실제 grep 명령이 있어야 한다
    cmds = [line for s in steps[build + 1:] for line in s.splitlines() if _structural(line) and "echo" not in line]
    after = "\n".join(cmds)
    assert re.search(r"\bgrep\b[^\n]*'mcp\.figma\.com'\s+dist\b", after), (
        "FE job: npm run build 뒤에 Figma 캡처 스크립트 검사가 없다"
    )
    assert re.search(r"<\s*dist/index\.html\s*\|\s*grep\b[^\n]*<script", after) and re.search(
        r"\bgrep\b[^\n]*src[^\n]*\(https\?:\|//\)", after
    ), "FE job: dist/index.html의 외부 <script src> 검사가 없다"
    assert re.search(
        r"\bgrep\b[^\n]*'localhost:3000\|AI_SERVICE_\(TOKEN\|URL\)\|DXWORKS_\|ai_contract'\s+dist\b", after
    ), "FE job: 개발 전용 안내 검사가 없다"


def test_dependabot_updates_github_actions():
    path = GITHUB_DIR / "dependabot.yml"
    assert path.is_file(), ".github/dependabot.yml이 없어 SHA로 고정한 액션이 갱신되지 않는다"
    text = path.read_text(encoding="utf-8")
    assert re.search(r"^version:\s*2\s*$", text, re.MULTILINE)
    assert re.search(r"package-ecosystem:\s*['\"]?github-actions['\"]?\s*$", text, re.MULTILINE)
    assert re.search(r"interval:\s*['\"]?weekly['\"]?\s*$", text, re.MULTILINE)
    assert re.search(r"^\s*groups:\s*$", text, re.MULTILINE)
