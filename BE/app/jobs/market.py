"""직무별 채용 시장 통계 배치.

    python -m app.jobs.market collect --role backend_developer --ids 55404303,55416896
    python -m app.jobs.market collect --role privacy_specialist --ids-file ids.txt   # 쉼표·줄바꿈으로 구분한 공고 번호
    python -m app.jobs.market snapshot                 # 공고가 있는 모든 직무의 통계를 AI로 계산해 저장
    python -m app.jobs.market export --out DIR         # 직무별 최신 통계를 JSON으로 (원문 없음)
    python -m app.jobs.market import --dir DIR         # export한 통계를 다른 DB에 넣는다 (수집 없이 통계만 쓸 때)

- collect: 공개 공고 상세 페이지를 천천히 읽어 market_postings에 저장한다 (app/integrations/job_sources.py의 원칙).
  출처의 직무 분류가 넓게 달려 있어 ROLE_FILTERS로 그 직무와 관련된 공고만 남긴다 (통과하지 못하면 그 직무에서 뺀다).
- snapshot: 직무마다 공고 묶음을 AI 서비스(POST /v1/market/snapshots)에 보내고, 응답이 보낸 직무·건수와 맞는지,
  해시가 맞는지 다시 확인한 뒤 market_snapshots에 쌓는다. AI_SERVICE_URL·AI_SERVICE_TOKEN이 필요하다.
- 접속 정보는 앱과 같은 DB_* 환경변수다.
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

from dotenv import load_dotenv
from pydantic import ValidationError

from app.ai_contract.market import MarketPosting, MarketRoleID, MarketSnapshot, MarketSnapshotRequest
from app.integrations.ai_service import AIServiceClient, AIServiceError, ai_service
from app.integrations.job_sources import SOURCES, SourceBlocked, SourceError
from app.services.market_store import MarketStore, market_store, snapshot_hash

logger = logging.getLogger("app.jobs.market")
ROLE_IDS: tuple[str, ...] = get_args(MarketRoleID)

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


def import_snapshots(directory: Path, *, store: MarketStore = market_store) -> dict:
    report = {}
    for path in sorted(directory.glob("*.json")):
        snapshot = MarketSnapshot.model_validate(json.loads(path.read_text(encoding="utf-8")))
        if snapshot_hash(snapshot) != snapshot.content_hash:
            raise ValueError(f"해시가 맞지 않는 통계 파일: {path.name}")
        report[snapshot.role_id] = store.save_snapshot(snapshot)
    return report


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
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    load_dotenv()
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
            return 1 if result["blocked"] else 0
        if args.command == "snapshot":
            roles = args.role or [r for r in ROLE_IDS if market_store.posting_counts().get(r)]
            report = asyncio.run(build_snapshots(roles))
            for role_id, item in report.items():
                logger.info("통계 %s: %s", role_id, item)
            return 0
        if args.command == "export":
            logger.info("내보낸 파일: %s", export_snapshots(args.out))
            return 0
        logger.info("가져온 통계: %s", import_snapshots(args.dir))
        return 0
    except (AIServiceError, RuntimeError, ValueError, OSError) as exc:
        detail = getattr(exc, "detail", None) or str(exc)
        logger.error("실패: %s", detail)
        return 1


if __name__ == "__main__":
    sys.exit(main())
