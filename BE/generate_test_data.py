import os
import sys
import signal
import stat
import uuid
import random
import json
import csv
from datetime import datetime, timedelta, timezone
import psycopg
from dotenv import load_dotenv
from app.core.config import db_password
from app.services.security import hash_password

load_dotenv()

# 생성되는 테스트 유저 전원의 공통 로그인 비밀번호
TEST_PASSWORD = "test1234"

# 다양한 배지 템플릿 풀 (논문 테스트용)
BADGE_TEMPLATES = [
    {"issuer": "한국산업인력공단", "name": "정보처리기사", "desc": "소프트웨어 개발 및 시스템 구축 역량 인증"},
    {"issuer": "한국산업인력공단", "name": "정보보안기사", "desc": "시스템 보안 및 네트워크 보안 역량 인증"},
    {"issuer": "TOEIC", "name": "TOEIC 900+", "desc": "비즈니스 영어 능통자 인증"},
    {"issuer": "OPIc", "name": "OPIc AL", "desc": "고급 영어 회화 역량 인증"},
    {"issuer": "경북대학교", "name": "AI 커리어 에이전트 해커톤 대상", "desc": "교내 해커톤 대상 수상"},
    {"issuer": "경북대학교", "name": "소프트웨어 공학 A+", "desc": "전공 핵심 과목 최우수 성적"},
    {"issuer": "경북대학교", "name": "캡스톤 디자인 최우수상", "desc": "졸업 프로젝트 최우수 평가"},
    {"issuer": "K-Digital Training", "name": "백엔드 개발자 부트캠프 수료", "desc": "6개월 백엔드 전문 교육 과정 수료"},
    {"issuer": "K-Digital Training", "name": "클라우드 아키텍처 부트캠프 수료", "desc": "AWS 클라우드 인프라 구축 과정 수료"},
    {"issuer": "AWS", "name": "AWS Certified Solutions Architect", "desc": "클라우드 솔루션 설계 역량 인증"},
    {"issuer": "Google", "name": "Google Cloud Professional", "desc": "GCP 클라우드 아키텍트 인증"},
    {"issuer": "삼성전자", "name": "SSAFY 11기 수료", "desc": "삼성 청년 SW 아카데미 수료"},
    {"issuer": "우아한형제들", "name": "우아한테크코스 6기 수료", "desc": "웹 백엔드 아키텍처 및 클린코드 과정 수료"},
    {"issuer": "네이버", "name": "네이버 부스트캠프 AI Tech 수료", "desc": "인공지능 실무 프로젝트 수료"},
    {"issuer": "카카오", "name": "카카오 테크 캠퍼스 수료", "desc": "카카오 실무 프로젝트 경험 인증"},
    {"issuer": "정보통신산업진흥원", "name": "오픈소스 기여 인증서", "desc": "글로벌 오픈소스 프로젝트 PR 병합 인증"},
    {"issuer": "KISA", "name": "사이버 가디언즈 해킹방어대회 우수상", "desc": "웹 취약점 분석 부문 우수상"},
    {"issuer": "Kaggle", "name": "Kaggle Competition Silver Medal", "desc": "데이터 분석 및 예측 모델링 상위 5%"},
    {"issuer": "백준", "name": "백준 알고리즘 Platinum V", "desc": "고급 알고리즘 및 자료구조 문제 해결 능력"},
    {"issuer": "프로그래머스", "name": "프로그래머스 코딩테스트 레벨 4", "desc": "고급 코딩테스트 문제 해결 능력"}
]

SURNAMES = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임", "한", "오", "서", "신", "권", "황", "안", "송", "류", "전", "홍", "고", "문", "양", "손", "배", "조", "백", "허", "유"]
FIRST_NAMES = ["민준", "서연", "도윤", "서윤", "시우", "지우", "민재", "서현", "주원", "하은", "지훈", "하윤", "건우", "민서", "현우", "지민", "우진", "채원", "선우", "지아", "서준", "윤서", "연우", "수아", "준혁", "다은", "승현", "은지", "태윤", "수진"]

def generate_random_date():
    start = datetime.now(timezone.utc) - timedelta(days=365 * 3) # 최근 3년
    end = datetime.now(timezone.utc)
    random_days = random.randrange((end - start).days)
    return start + timedelta(days=random_days)

def get_db_connection():
    return psycopg.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        user=os.getenv("DB_USER", "postgres"),
        # 앱과 같은 규칙: 개발은 로컬 기본값, APP_ENV=production은 비었거나 기본값이면 접속하지 않는다 (app/core/config.py)
        password=db_password(),
        dbname=os.getenv("DB_NAME", "vc_career_agent"),
        autocommit=True
    )

def _stop_on_signal(signum, frame):
    # SIGTERM(kill의 기본 신호 등)·SIGHUP(터미널 닫힘)도 Ctrl+C처럼 예외로 멈춰 다른 실패와 같은 정리(롤백·임시 파일 삭제)를
    # 거친다. (docker stop은 컨테이너의 첫 프로세스에만 SIGTERM을 보내므로, docker compose exec로 띄운 이 스크립트는 신호 없이
    # 강제 종료된다.) 커밋 전에만 쓰며(main의 on_signal) 이 한 줄을 stderr에 남기고 종료 코드 1. 커밋 뒤에는 이 줄을 내지 않는다.
    raise SystemExit(f"에러 발생: {signal.Signals(signum).name} 신호를 받아 중단했습니다.")

def main():
    # 공개된 비밀번호(TEST_PASSWORD)로 계정을 만드는 로컬·개발 전용 스크립트: migrate --seed처럼 운영에서는 DB에 접속하기 전에
    # 거부한다. APP_ENV는 앱과 같이 읽는다(비우면 development, development가 아니면 운영). Compose는 APP_ENV=development.
    if (os.getenv("APP_ENV", "").strip().lower() or "development") != "development":
        print("에러 발생: 테스트 데이터 생성(공개된 비밀번호의 테스트 계정)은 로컬·개발 전용이라 "
              "APP_ENV=development(또는 빈 값)에서만 실행합니다.", file=sys.stderr)
        sys.exit(1)
    # CSV를 저장할 이름(현재 폴더의 test_users_did_list.csv)이 폴더면 CSV를 쓸 수 없으므로 DB 작업 전에 멈춘다.
    csv_filename = "test_users_did_list.csv"
    if os.path.isdir(csv_filename):
        print(f"에러 발생: 현재 폴더의 '{csv_filename}'가 폴더라 CSV를 저장할 수 없어 DB 작업 전에 멈춥니다(DB는 그대로). "
              "그 폴더를 옮기거나 이름을 바꾼 뒤 다시 실행하세요.", file=sys.stderr)
        sys.exit(1)
    print("[논문 테스트용] 임시 유저 및 배지 더미 데이터 생성을 시작합니다...")
    
    users = []
    created = 0
    # CSV는 커밋 전에(트랜잭션 안에서) 같은 폴더의 임시 파일에 먼저 쓰고, 커밋한 뒤 os.replace로 CSV 이름으로 바꾼다.
    # 같은 폴더(같은 파일 시스템)라 이름 바꾸기는 한 번에 끝나고, 임시 파일은 어떤 경로로 끝나든 남기지 않는다(finally).
    # CSV가 심볼릭 링크면 예전 덮어쓰기처럼 링크는 그대로 두고 가리키는 파일을 바꾼다(임시 파일은 그 파일과 같은 폴더).
    csv_target = os.path.realpath(csv_filename) if os.path.islink(csv_filename) else csv_filename
    tmp_filename = f"{csv_target}.{uuid.uuid4().hex[:8]}.tmp"
    tmp_created = False
    conn = None
    # SIGTERM·SIGHUP(있는 OS에서만)·Ctrl+C(SIGINT)는 아래 on_signal로 받아 단계(stage)마다 다르게 처리하고, finally의 맨
    # 마지막에 원래 처리기로 되돌린다. 무시하도록 물려받은 신호(nohup으로 띄운 실행의 SIGHUP 등)와 처리기가 없는(SIG_IGN·SIG_DFL)
    # Ctrl+C는 예전처럼 그대로 둔다(처리기를 두지도 되돌리지도 않음).
    #   run    : 커밋 전. SIGTERM·SIGHUP은 '신호를 받아 중단했습니다' 한 줄(_stop_on_signal), Ctrl+C는 원래 처리기
    #            (KeyboardInterrupt)로 멈춰 롤백·임시 파일 삭제를 거친다.
    #   commit : COMMIT을 보내고 연결을 닫을 때까지. 받은 신호는 미뤘다가(held) 커밋 뒤에 처리한다.
    #   after  : 커밋 뒤(이름 바꾸기·완료 안내). 중단하면 아래 except에서 DB 변경이 남았다고 알리거나(CSV 저장 전) 더 알리지
    #            않고 종료 코드 1(CSV도 저장한 뒤). 커밋 전 안내는 내지 않는다.
    #   cleanup: 커밋 뒤 실패 안내 중과 finally(어느 경로든)의 정리 중. 받은 신호는 미뤄서(커밋 전 안내·traceback 없이)
    #            안내·임시 파일 삭제·처리기 복원을 끝까지 하고, 다 저장한 실행이면 끝에서 더 알리지 않고 종료 코드 1.
    # 단계는 처리기를 바꿔 끼우지 않고 대입 한 번으로 바꾼다(바꾸는 동안 닿은 신호가 이전 단계의 처리로 새지 않게).
    stage = "run"
    held = []  # commit·cleanup 단계에서 받아 미뤄 둔 신호
    old_handlers = {}  # on_signal을 둔 신호 → 원래 처리기

    def on_signal(signum, frame):
        if stage in ("commit", "cleanup"):
            held.append(signum)
        elif signum == signal.SIGINT:
            old_handlers[signum](signum, frame)  # Ctrl+C는 원래 처리기로 (기본은 KeyboardInterrupt)
        elif stage == "run":
            _stop_on_signal(signum, frame)
        else:
            raise SystemExit(1)  # 커밋 뒤: 아래 except가 DB 변경이 남았다고 알리거나 조용히 종료 코드 1로 끝낸다

    if callable(signal.getsignal(signal.SIGINT)):
        old_handlers[signal.SIGINT] = signal.signal(signal.SIGINT, on_signal)
    for name in ("SIGTERM", "SIGHUP"):
        signum = getattr(signal, name, None)
        if signum is not None and signal.getsignal(signum) is not signal.SIG_IGN:
            old_handlers[signum] = signal.signal(signum, on_signal)
    finished = False  # DB 변경·CSV 저장·완료 안내를 모두 마쳤는지

    try:
        # 유저·배지 INSERT와 CSV 임시 파일 쓰기를 한 트랜잭션으로: 중간에 실패하면 DB에 아무것도 남기지 않는다 (CSV도 그대로).
        conn = get_db_connection()
        with conn.transaction():
            with conn.cursor() as cur:
                print("1. 유저 45명 생성 중 (이미 있는 이메일은 그 계정을 그대로 씀)...")
                test_password_hash = hash_password(TEST_PASSWORD)
                for i in range(45):
                    user_id = str(uuid.uuid4())
                    name = f"{random.choice(SURNAMES)}{random.choice(FIRST_NAMES)}"
                    email = f"user{i+1:03d}@example.com"
                    did = f"did:key:z6MkMockTestUser{i+1:03d}{uuid.uuid4().hex[:8]}"
                    now_str = datetime.now(timezone.utc).isoformat()
                    
                    cur.execute(
                        "INSERT INTO users (id, email, name, subject_did, password_hash, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id, subject_did, name",
                        (user_id, email, name, did, test_password_hash, now_str, now_str)
                    )
                    row = cur.fetchone()
                    if row is None:
                        # 이미 있는 계정(Compose 시드 DB의 user001~045 등): 이름·DID·비밀번호는 그대로 두고 배지만 그 계정에 넣는다.
                        cur.execute("SELECT id, subject_did, name FROM users WHERE email = %s", (email,))
                        row = cur.fetchone()
                    else:
                        created += 1
                    user_id, did, name = str(row[0]), row[1], row[2]
                    
                    badge_count = random.randint(5, 15)
                    users.append({
                        "name": name,
                        "email": email,
                        "did": did,
                        "user_id": user_id,
                        "badge_count": badge_count
                    })

                print("2. 유저별 배지(5~15개) 랜덤 배분 및 생성 중...")
                total_badges = 0
                for user in users:
                    # 유저별로 템플릿을 중복 없이 뽑기 위해
                    selected_templates = random.sample(BADGE_TEMPLATES, user["badge_count"])
                    
                    for tmpl in selected_templates:
                        record_id = str(uuid.uuid4())
                        issued_date = generate_random_date()
                        
                        raw_data = {
                            "schema_version": "0.1.0",
                            "format": "OB3.0",
                            "issuer": { "name": tmpl["issuer"], "trust_level": "accredited" },
                            "achievement": {
                                "name": tmpl["name"],
                                "description": tmpl["desc"]
                            }
                        }
                        
                        cur.execute(
                            """
                            INSERT INTO credentials (
                                record_id, user_id, source_provider, source_external_id, format, 
                                achievement_name, issuer_name, issuer_trust_level, status, 
                                issued_at, awarded_date, raw_data, created_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT DO NOTHING
                            """,
                            (
                                record_id, user["user_id"], "mock_seeder", f"ext-{record_id[:8]}", "OB3.0",
                                tmpl["name"], tmpl["issuer"], "accredited", "verified",
                                issued_date.isoformat(), issued_date.date().isoformat(),
                                json.dumps(raw_data), datetime.now(timezone.utc).isoformat()
                            )
                        )
                        total_badges += 1

            # 3. CSV 파일로 유저 DID 목록 저장: 커밋 전에 임시 파일에 쓴다. 여기서 실패하면(쓰기 권한·디스크 부족 등) DB도 롤백된다.
            with open(tmp_filename, 'x', encoding='utf-8-sig', newline='') as f:
                tmp_created = True
                writer = csv.writer(f)
                writer.writerow(["이름", "이메일", "발급된 배지 개수", "조회용 DID (이 값을 복사해서 쓰세요)"])
                for u in users:
                    writer.writerow([u["name"], u["email"], f"{u['badge_count']}개", u["did"]])
            # 예전처럼 그 자리에서 다시 쓴 것과 같게: 기존 CSV의 권한 비트를(root로 실행하면 소유자·그룹도) 임시 파일에 옮긴다.
            # Compose는 root로 실행하므로, 옮기지 않으면 Linux 호스트에서 CSV가 root 소유 0644로 바뀐다. 새 CSV는 지금처럼
            # 0o666 & ~umask. Windows는 권한이 읽기 전용 표시뿐이라 옮기지 않는다(읽기 전용 CSV는 어차피 바꿀 수 없다).
            if hasattr(os, "geteuid") and os.path.exists(csv_target):
                st = os.stat(csv_target)
                if os.geteuid() == 0:
                    os.chown(tmp_filename, st.st_uid, st.st_gid)
                os.chmod(tmp_filename, stat.S_IMODE(st.st_mode))
            # 커밋 지점: 여기서 트랜잭션을 나가며 COMMIT하고 아래에서 연결을 닫을 때까지 받은 Ctrl+C·SIGTERM·SIGHUP은
            # 미뤘다가 커밋 뒤(아래 try)에서 처리한다. COMMIT 응답 직후에 닿은 신호도 '커밋 전 중단'처럼 알리지 않기 위해서다.
            stage = "commit"

        # 여기서부터는 커밋 뒤다. 연결을 닫고 임시 파일을 CSV 이름으로 바꾼다. 여기서 실패하거나 중단되면(예: Windows에서
        # CSV를 엑셀로 열어 둠, Ctrl+C·SIGTERM, 커밋하는 동안 미뤄 둔 신호) DB 변경은 그대로 남는다고 알린다.
        try:
            conn.close()
            stage = "after"
            for signum in held:
                on_signal(signum, None)  # 미뤄 둔 신호를 지금 처리한다(커밋 뒤 중단)
            held.clear()
            os.replace(tmp_filename, csv_target)
        except BaseException as e:
            stage = "cleanup"  # 이 안내를 남기는 동안 받은 신호는 미룬다(안내가 커밋 전 중단 안내·traceback으로 바뀌지 않게)
            if not isinstance(e, Exception) and not os.path.exists(tmp_filename):
                sys.exit(1)  # 이름 바꾸기가 끝난 뒤의 중단: CSV도 이미 저장되었으므로 더 알리지 않고 종료 코드 1
            reason = e if isinstance(e, Exception) else "중단되었습니다(Ctrl+C 또는 종료 신호)"
            print(f"에러 발생: DB 변경(새로 만든 유저 {created}명, 배지 {total_badges}개)은 이미 커밋되어 그대로 남지만 "
                  f"CSV 파일({csv_filename})은 저장하지 못했습니다: {reason}", file=sys.stderr)
            sys.exit(1)

        try:
            print(f"완료! 총 45명의 유저(새로 만듦 {created}명, 이미 있던 계정 {len(users) - created}명)에게 "
                  f"{total_badges}개의 배지가 성공적으로 생성되었습니다.")
            print(f"유저 목록과 조회용 DID가 '{csv_filename}' 파일로 저장되었습니다. 열어서 확인해 보세요!")
            print(f"새로 만든 테스트 유저의 로그인 비밀번호는 '{TEST_PASSWORD}' 입니다 "
                  f"(이미 있던 계정은 비밀번호를 바꾸지 않습니다. 시드 계정은 '{TEST_PASSWORD}').")
        except BaseException:
            sys.exit(1)  # DB 변경·CSV를 모두 저장한 뒤의 중단·출력 실패: 커밋 전 안내 없이 종료 코드 1
        finished = True

    except Exception as e:
        # 실패하면 오류 내용 전체(DB 오류는 LINE·CONTEXT 줄이 붙어 여러 줄일 수 있음)를 stderr에 남기고 종료 코드 1.
        # DB 작업·CSV 임시 파일 쓰기(커밋 전)에서 난 오류면 DB 변경은 트랜잭션째 롤백되고 CSV도 바뀌지 않는다.
        print(f"에러 발생: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        # 실패·중단(Ctrl+C·SIGTERM·SIGHUP 포함) 어느 경로로 끝나도 임시 파일은 지운다. 이름을 바꿨으면 이미 없다.
        # 정리하는 동안 받은 신호는 미뤄서(두 번째 신호에도) 이 정리를 끝까지 하고, 원래 처리기는 맨 마지막에 되돌린다.
        stage = "cleanup"
        try:
            if tmp_created and os.path.exists(tmp_filename):
                os.remove(tmp_filename)
            if conn is not None and not conn.closed:
                conn.close()  # 커밋 전에 실패·중단한 연결 (트랜잭션은 이미 롤백)
        finally:
            for signum, handler in old_handlers.items():  # 정리가 실패해도 원래 처리기는 되돌린다
                signal.signal(signum, handler)
        if finished and held:
            sys.exit(1)  # DB 변경·CSV를 모두 저장한 뒤 정리 중에 받은 중단: 더 알리지 않고 종료 코드 1

if __name__ == "__main__":
    main()
