"""직무별 채용 시장 통계 배치.

    python -m app.jobs.market collect --role backend_developer --ids 55404303,55416896
    python -m app.jobs.market collect --role privacy_specialist --ids-file ids.txt   # 쉼표·줄바꿈으로 구분한 공고 번호
    python -m app.jobs.market snapshot                 # 공고가 있는 모든 직무의 통계를 AI로 계산해 저장
    python -m app.jobs.market export --out DIR         # 직무별 최신 통계를 JSON으로 (원문 없음)
    python -m app.jobs.market import --dir DIR         # export한 통계를 다른 DB에 넣는다 (수집 없이 통계만 쓸 때)

- collect: 공개 공고 상세 페이지를 천천히 읽어 market_postings에 저장한다 (app/integrations/job_sources.py의 원칙).
  출처의 직무 분류가 넓게 달려 있어 ROLE_FILTERS로 그 직무와 관련된 공고만 남긴다 (통과하지 못하면 그 직무에서 뺀다).
- snapshot: 직무마다 공고 묶음을 AI 서비스(POST /v1/market/snapshots)에 보내고, 응답이 보낸 직무·건수와 맞는지,
  해시가 맞는지 다시 확인한 뒤 market_snapshots에 쌓는다.
- import: 파일의 해시를 다시 계산해 확인하고, 기준 시각(as_of)이 미래이거나 이름 글자 수가 한도를 넘거나 건수가 음수·DB
  정수 범위 밖인 파일은 받지 않는다. 검사에서 하나라도 걸리면 아무 파일도 넣지 않는다 (저장 단계의 DB 오류는 import_snapshots 참고).
  해시는 열쇠 없는 SHA-256이라 실수로 깨진 파일만 걸러 낸다 (파일을 고친 사람은 해시도 다시 계산할 수 있다).
- 환경변수: 모든 명령이 앱과 같은 DB_*로 접속한다 (BE/.env가 있으면 읽는다). collect·export·import는 그것만 있으면 된다.
  snapshot은 AI 서비스 클라이언트가 앱 설정(get_settings)을 거치므로 AI_SERVICE_URL·AI_SERVICE_TOKEN과 함께,
  APP_ENV=production이면 서버와 같은 운영 비밀값(JWT_SECRET·HOLDER_KEY_ENCRYPTION_KEY 등)도 있어야 시작한다.
  로그 수준은 서버처럼 LOG_LEVEL을 따른다 (비우면 INFO).
- 종료 코드: 아무 일도 하지 못했으면 1이다 (import할 파일 없음, collect가 막혔거나 한 건도 읽지 못함, snapshot을 만든 직무 없음).
  실패 이유는 한 줄로 남긴다. 명령줄 인자나 LOG_LEVEL 값이 틀리면 아무것도 하지 않고 2다 (argparse의 오류 한 줄).
"""
import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, get_args

import psycopg
from dotenv import load_dotenv
from fastapi import HTTPException
from pydantic import ValidationError

from app.ai_contract.market import MarketPosting, MarketRoleID, MarketSnapshot, MarketSnapshotRequest
from app.core.config import log_level
from app.integrations.ai_service import AIServiceClient, ai_service
from app.integrations.job_sources import SOURCES, SourceBlocked, SourceError
from app.services.database import DatabaseBusy
from app.services.market_store import MarketStore, market_store, snapshot_hash

logger = logging.getLogger("app.jobs.market")
ROLE_IDS: tuple[str, ...] = get_args(MarketRoleID)
# import가 받는 통계 파일의 한도. 계약 모델(MarketSnapshot)에는 글자 수 제한이 없어 BE에서 본다 (계약 파일은 AI와 같아야 해서
# 고치지 않는다). 이름·ID·버전은 AI 카탈로그의 가장 긴 한도(직무·기술 이름 80자)에 맞추고, 경고는 코드 한 줄씩이다.
MAX_IMPORT_LABEL_CHARS = 80
MAX_IMPORT_WARNING_CHARS = 200
MAX_IMPORT_WARNINGS = 20
# 건수(표본 수·중복 수·회사 수, 기술·기술 쌍마다의 공고 수)의 범위: 0 이상, market_snapshots.sample_size(INTEGER)에 들어가는 값까지.
# 넘는 값은 저장할 때 DB가 거절하므로(integer out of range) 검사 단계에서 먼저 거른다.
MAX_IMPORT_COUNT = 2**31 - 1
# 기준 시각(as_of)이 지금보다 이만큼 넘게 앞선 파일은 받지 않는다 (서버 사이의 시계 차이만 봐준다).
# 최신 통계는 as_of가 가장 늦은 행이라, 먼 미래의 as_of가 들어가면 그 뒤에 계산한 통계가 영영 보이지 않는다.
MAX_IMPORT_CLOCK_SKEW = timedelta(minutes=5)
# 실패 이유 한 줄의 글자 수 상한 (형식 검사 실패는 항목마다 여러 줄을 낸다)
MAX_FAILURE_LINE_CHARS = 500

_PRIVACY_TITLE = re.compile(r"개인정보|프라이버시|privacy|컴플라이언스|compliance|isms|정보보호\s*(?:관리|인증|컨설|담당)|"
                            r"보안\s*(?:컨설|인증)|cpo|grc", re.IGNORECASE)


def _privacy_related(posting: MarketPosting) -> bool:
    """제목이 개인정보·컴플라이언스 직무이거나, 업무·자격요건이 개인정보를 두 번 이상 다루는 공고."""
    body = posting.responsibilities + "\n" + posting.qualifications
    return bool(_PRIVACY_TITLE.search(posting.title)) or len(re.findall(r"개인정보", body)) >= 2


def _title_or_stack(title_pattern: str, stack_pattern: str) -> Callable[[MarketPosting], bool]:
    """제목이 그 직무를 가리키거나 기술스택에 그 직무의 핵심 기술이 하나라도 있는 공고."""
    title, stack = re.compile(title_pattern, re.IGNORECASE), re.compile(stack_pattern, re.IGNORECASE)
    return lambda posting: bool(title.search(posting.title)) or any(stack.search(item) for item in posting.tech_stack)


# 출처의 직무 분류는 넓게 달린다(예: 프런트엔드 분류에 Java·JSP 운영 공고). 직무와 무관한 공고가 통계를 흐리지 않게
# 직무마다 관련 공고 판별식을 둔다. 통과하지 못한 공고는 그 직무에서 뺀다.
ROLE_FILTERS: dict[str, Callable[[MarketPosting], bool]] = {
    "privacy_specialist": _privacy_related,
    "frontend_developer": _title_or_stack(
        r"프론트|프런트|front|퍼블리|publisher|\bFE\b|react|vue|angular|웹\s*UI",
        r"^(react|vue|angular|svelte|next\.?js|nuxt|typescript|javascript|es6|html|css)"),
    "backend_developer": _title_or_stack(
        r"백엔드|back[\s-]?end|서버|server|\bapi\b|풀스택|full[\s-]?stack|\bBE\b",
        r"^(java$|java\s|spring|kotlin|python|django|fastapi|flask|node\.?js|nestjs|go$|golang|php|c#|\.net|asp\.net|"
        r"ruby|rust|scala|mysql|mariadb|postgresql|oracle|mssql|redis|mongodb|kafka|jpa|mybatis|rest api|graphql|grpc)"),
    "bigdata_engineer": _title_or_stack(
        r"데이터|data|빅데이터|\bETL\b|\bDW\b|\bDBA\b|분석|머신러닝|\bML\b|\bAI\b",
        r"^(spark|pyspark|hadoop|hive|kafka|airflow|flink|bigquery|redshift|snowflake|databricks|dbt|presto|trino|"
        r"etl|elasticsearch|sql$|python|scala|pandas|tableau|hbase|kubeflow|mlflow)"),
    "infra_engineer": _title_or_stack(
        r"인프라|infra|devops|데브옵스|\bSRE\b|시스템\s*(?:엔지니어|운영|관리)|system|클라우드|cloud|네트워크|network|"
        r"서버\s*(?:운영|관리|엔지니어)|플랫폼\s*엔지니어|\bTA\b|\bSE\b",
        r"^(aws|amazon|gcp|google cloud|azure|ncp|kubernetes|k8s|docker|terraform|ansible|linux|centos|ubuntu|jenkins|"
        r"argo|helm|prometheus|grafana|nginx|vmware|openstack|github actions|gitlab)"),
}


def parse_ids(text: str) -> list[str]:
    ids = []
    for token in re.split(r"[\s,]+", text.strip()):
        if token and token not in ids:
            if not re.fullmatch(r"[0-9]{1,12}", token):
                raise ValueError(f"공고 번호가 아닙니다: {token[:20]}")
            ids.append(token)
    return ids


def collect(role_id: str, ids: list[str], *, source, store: MarketStore = market_store,
            reuse_hours: float = 24.0, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict:
    """공고 번호들을 읽어 저장한다. 출처가 막으면(403·429) 거기서 멈춘다.

    reuse_hours 안에 이미 수집한 공고는 출처에 다시 요청하지 않고 저장본에 직무만 덧붙인다
    (한 공고가 여러 직무 분류에 걸쳐 있을 때 같은 페이지를 거듭 읽지 않는다). 0이면 항상 새로 읽는다.
    """
    result = {"created": 0, "updated": 0, "unchanged": 0, "filtered": 0, "failed": 0, "blocked": False,
              "reused": 0}
    relevant = ROLE_FILTERS.get(role_id, lambda posting: True)
    for index, external_id in enumerate(ids, start=1):
        try:
            posting = (store.recent_posting(source.name, external_id, now() - timedelta(hours=reuse_hours))
                       if reuse_hours > 0 else None)
            if posting is not None:
                result["reused"] += 1
            else:
                posting = MarketPosting(**source.fetch(external_id))
        except SourceBlocked as exc:
            logger.error("수집 중단 (%d/%d): %s", index, len(ids), exc)
            result["blocked"] = True
            break
        except (SourceError, ValidationError) as exc:
            reason = str(exc) if isinstance(exc, SourceError) else "형식 검사 실패"
            logger.warning("건너뜀 %s: %s", external_id, reason)
            result["failed"] += 1
            continue
        if not relevant(posting):
            # 전에 이 직무로 저장해 둔 공고라면(판별식이 생기기 전·바뀌기 전) 그 직무에서 뺀다.
            store.remove_role(posting.source, posting.external_id, role_id)
            result["filtered"] += 1
            continue
        result[store.upsert_posting(role_id, posting, source.rights_basis)] += 1
        if index % 20 == 0:
            logger.info("진행 %d/%d", index, len(ids))
    return result


async def build_snapshots(role_ids: list[str], *, ai: AIServiceClient = ai_service, store: MarketStore = market_store,
                          now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict:
    """직무별로 AI에 통계를 맡기고 저장한다. {role_id: {'sample_size', 'saved'} | {'skipped': 이유}}"""
    report = {}
    for role_id in role_ids:
        postings = await asyncio.to_thread(store.postings, role_id)
        if not postings:
            report[role_id] = {"skipped": "no postings"}
            continue
        output = await ai.market_snapshot(MarketSnapshotRequest(role_id=role_id, as_of=now(), postings=postings))
        snapshot = output.snapshot
        # AI 응답을 그대로 믿지 않는다: 직무·건수·해시가 보낸 것과 맞아야 저장한다.
        if (snapshot.role_id != role_id
                or snapshot.sample_size + snapshot.duplicates_removed != len(postings)
                or len(output.extractions) != snapshot.sample_size
                or snapshot_hash(snapshot) != snapshot.content_hash):
            raise RuntimeError(f"AI 통계 응답이 보낸 공고와 맞지 않습니다: {role_id}")
        saved = await asyncio.to_thread(store.save_snapshot, snapshot)
        report[role_id] = {"sample_size": snapshot.sample_size, "saved": saved,
                           "top": [s.label for s in snapshot.skills[:5]]}
    return report


def export_snapshots(out_dir: Path, *, store: MarketStore = market_store) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for snapshot in store.latest_all():
        path = out_dir / f"{snapshot.role_id}.json"
        path.write_text(json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        written.append(path.name)
    return sorted(written)


def import_snapshots(directory: Path, *, store: MarketStore = market_store,
                     now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict:
    """directory의 *.json을 모두 검사한 뒤 저장한다. {role_id: 새로 저장했는지}

    검사(형식·해시·기준 시각·글자 수·건수 범위)에서 하나라도 받지 못하면 아무것도 넣지 않는다. '모두 아니면 전무'는 이 검사까지다:
    검사를 모두 통과한 뒤의 저장은 파일마다 따로 커밋하므로, 저장하는 중에 DB 오류(접속 끊김 등)가 나면 그 앞의 파일은 이미
    들어가 있다 (다시 실행하면 이미 들어간 같은 내용은 건너뛴다).
    """
    paths = sorted(directory.glob("*.json"))
    if not paths:  # 없는 폴더, 빈 폴더, 폴더가 아닌 경로
        raise ValueError(f"통계 JSON 파일이 없습니다: {directory}")
    latest = now() + MAX_IMPORT_CLOCK_SKEW
    snapshots = []
    for path in paths:
        snapshot = MarketSnapshot.model_validate(json.loads(path.read_text(encoding="utf-8")))
        if snapshot_hash(snapshot) != snapshot.content_hash:
            raise ValueError(f"해시가 맞지 않는 통계 파일: {path.name}")
        if snapshot.as_of > latest:
            raise ValueError(f"기준 시각(as_of)이 미래인 통계 파일: {path.name}")
        labels = [snapshot.role_label, snapshot.catalog_version, snapshot.extractor_version,
                  *(source.source for source in snapshot.sources),
                  *(text for skill in snapshot.skills for text in (skill.skill_id, skill.label)),
                  *(text for pair in snapshot.pairs for text in (pair.skill_a, pair.skill_b))]
        if (any(len(text) > MAX_IMPORT_LABEL_CHARS for text in labels) or len(snapshot.warnings) > MAX_IMPORT_WARNINGS
                or any(len(text) > MAX_IMPORT_WARNING_CHARS for text in snapshot.warnings)):
            raise ValueError(f"이름·경고의 글자 수나 경고 개수가 한도를 넘는 통계 파일: {path.name}")
        counts = [snapshot.sample_size, snapshot.duplicates_removed, snapshot.companies,
                  *(count for skill in snapshot.skills for count in (
                      skill.mentioned_postings, skill.required_postings, skill.stack_postings, skill.preferred_postings)),
                  *(pair.postings for pair in snapshot.pairs)]
        if any(not 0 <= count <= MAX_IMPORT_COUNT for count in counts):
            raise ValueError(f"건수가 음수이거나 DB 정수 범위를 넘는 통계 파일: {path.name}")
        snapshots.append(snapshot)
    return {snapshot.role_id: store.save_snapshot(snapshot) for snapshot in snapshots}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs.market", description="직무별 채용 시장 통계 배치")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("collect", help="공고 상세 페이지를 읽어 저장")
    c.add_argument("--role", required=True, choices=ROLE_IDS)
    c.add_argument("--source", default="jumpit", choices=sorted(SOURCES))
    c.add_argument("--ids", default="", help="쉼표로 구분한 공고 번호")
    c.add_argument("--ids-file", type=Path, help="공고 번호 파일 (쉼표·줄바꿈 구분)")
    c.add_argument("--delay", type=float, default=1.5, help="요청 간격(초, 최소 1)")
    c.add_argument("--reuse-hours", type=float, default=24.0, help="이 시간 안에 수집한 공고는 다시 요청하지 않음")
    s = sub.add_parser("snapshot", help="AI 서비스로 직무별 통계 계산")
    s.add_argument("--role", action="append", choices=ROLE_IDS, help="생략하면 공고가 있는 모든 직무")
    e = sub.add_parser("export", help="직무별 최신 통계를 JSON으로")
    e.add_argument("--out", type=Path, required=True)
    i = sub.add_parser("import", help="export한 통계 JSON을 DB에 넣기")
    i.add_argument("--dir", type=Path, required=True)
    args = parser.parse_args(argv)
    load_dotenv()  # 로컬 실행용 BE/.env. 컨테이너에서는 이미 있는 환경변수가 우선한다.
    # 서버(main.py)와 같은 규칙: LOG_LEVEL을 따르고(비우면 INFO), 요청마다 전체 URL(출처 공고 주소·AI 서비스 호스트)을 남기는
    # httpx 로그와 접속 호스트·응답 헤더를 남기는 httpcore 로그는 LOG_LEVEL=DEBUG일 때만 낸다.
    try:
        level = log_level()  # 없는 수준 이름이면 RuntimeError (app.core.config)
        logging.basicConfig(level=level, format="%(levelname)s:%(name)s:%(message)s")
    except (RuntimeError, ValueError):
        # logging이 모르는 수준 이름(LOG_LEVEL=verbose 등): traceback 대신 인자 오류처럼 한 줄을 남기고 종료 코드 2로 끝낸다.
        parser.error("LOG_LEVEL은 DEBUG, INFO, WARNING, ERROR, CRITICAL 중 하나여야 합니다 (비우면 INFO)")
    if level != "DEBUG":
        for name in ("httpx", "httpcore", "httpx2", "httpcore2"):
            logging.getLogger(name).setLevel(logging.WARNING)
    try:
        if args.command == "collect":
            text = args.ids + "\n" + (args.ids_file.read_text(encoding="utf-8") if args.ids_file else "")
            ids = parse_ids(text)
            if not ids:
                parser.error("--ids 또는 --ids-file로 공고 번호를 주세요")
            source = SOURCES[args.source](delay_seconds=args.delay)
            try:
                result = collect(args.role, ids, source=source, reuse_hours=args.reuse_hours)
            finally:
                source.close()
            logger.info("수집 결과 %s: %s", args.role, result)
            if result["failed"] == len(ids):
                # 출처의 페이지 형식이 바뀌었거나 번호가 모두 틀렸다: 성공으로 끝내면 뒤의 snapshot이 예전 공고로 돈다.
                logger.error("실패: 공고를 한 건도 읽지 못했습니다 (%d건 모두 실패)", len(ids))
            return 1 if result["blocked"] or result["failed"] == len(ids) else 0
        if args.command == "snapshot":
            roles = args.role or [r for r in ROLE_IDS if market_store.posting_counts().get(r)]
            report = asyncio.run(build_snapshots(roles))
            for role_id, item in report.items():
                logger.info("통계 %s: %s", role_id, item)
            if not any("sample_size" in item for item in report.values()):
                logger.error("실패: 통계를 만든 직무가 없습니다 (수집된 공고 없음)")
                return 1
            return 0
        if args.command == "export":
            logger.info("내보낸 파일: %s", export_snapshots(args.out))
            return 0
        logger.info("가져온 통계: %s", import_snapshots(args.dir))
        return 0
    # HTTPException: AI 서비스 오류(AIServiceError)와 저장된 통계의 무결성 실패(market_store의 409).
    # RuntimeError: 설정 오류(app.core.config, 변수 이름만 담는다)와 AI 응답 불일치. ValueError: 공고 번호·통계 파일 검사 실패.
    # psycopg.Error·DatabaseBusy: DB 접속·쿼리 실패. OverflowError: 너무 큰 --reuse-hours·--delay.
    # 어느 것이든 traceback 없이 한 줄로 남긴다 (여러 줄 메시지는 한 줄로 잇고 길면 자른다).
    except (HTTPException, RuntimeError, ValueError, OSError, OverflowError, psycopg.Error, DatabaseBusy) as exc:
        detail = getattr(exc, "detail", None) or str(exc) or type(exc).__name__
        logger.error("실패: %s", " ".join(str(detail).split())[:MAX_FAILURE_LINE_CHARS])
        return 1


if __name__ == "__main__":
    sys.exit(main())
