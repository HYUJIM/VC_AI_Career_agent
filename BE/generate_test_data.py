import os
import uuid
import random
import json
import csv
from datetime import datetime, timedelta, timezone
import psycopg
from dotenv import load_dotenv

load_dotenv()

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
        password=os.getenv("DB_PASSWORD", "password"),
        dbname=os.getenv("DB_NAME", "vc_career_agent"),
        autocommit=True
    )

def main():
    print("[논문 테스트용] 임시 유저 및 배지 더미 데이터 생성을 시작합니다...")
    
    users = []
    
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                print("1. 유저 45명 생성 중...")
                for i in range(45):
                    user_id = str(uuid.uuid4())
                    name = f"{random.choice(SURNAMES)}{random.choice(FIRST_NAMES)}"
                    email = f"user{i+1:03d}@example.com"
                    did = f"did:key:z6MkMockTestUser{i+1:03d}{uuid.uuid4().hex[:8]}"
                    now_str = datetime.now(timezone.utc).isoformat()
                    
                    cur.execute(
                        "INSERT INTO users (id, email, name, subject_did, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        (user_id, email, name, did, now_str, now_str)
                    )
                    
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

        # 3. CSV 파일로 유저 DID 목록 저장
        csv_filename = "test_users_did_list.csv"
        with open(csv_filename, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["이름", "이메일", "발급된 배지 개수", "조회용 DID (이 값을 복사해서 쓰세요)"])
            for u in users:
                writer.writerow([u["name"], u["email"], f"{u['badge_count']}개", u["did"]])
                
        print(f"완료! 총 45명의 유저와 {total_badges}개의 배지가 성공적으로 생성되었습니다.")
        print(f"유저 목록과 조회용 DID가 '{csv_filename}' 파일로 저장되었습니다. 열어서 확인해 보세요!")

    except Exception as e:
        print(f"에러 발생: {e}")

if __name__ == "__main__":
    main()
