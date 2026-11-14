"""직무별 시장 통계용 직무·기술 사전.

- 직무 5개: 빅데이터 개발자, 개인정보보호 전문가, 백엔드 개발자, 프런트엔드 개발자, 인프라 개발자.
- 기술 사전은 직무 공통 하나다(예: Python은 백엔드·빅데이터 공고 모두에서 센다). 어떤 기술이 그 직무에서
  중요한지는 사전이 아니라 수집한 공고 통계가 정한다.
- 기존 직무 비교 카탈로그(src/career/skill_catalog.py)와 겹치는 기술은 같은 skill_id를 쓴다
  (sql, python, scala, spark, kafka, airflow, etl, data_modeling, git, linux, aws, docker).
- 사전을 바꾸면 CATALOG_VERSION을 올린다. 스냅샷에 버전이 남아 어떤 사전으로 센 통계인지 알 수 있다.
"""

import re
from dataclasses import dataclass, field

from src.career.contracts.market import MarketCatalog, MarketRole, MarketSkill
from src.career.skill_catalog import normalized_text

CATALOG_VERSION = "2026-11-14.1"

ROLES = [
    MarketRole(
        role_id="bigdata_engineer",
        label="빅데이터 개발자",
        description="데이터 파이프라인·저장소·분산 처리 플랫폼을 만들고 운영하는 직무",
    ),
    MarketRole(
        role_id="privacy_specialist",
        label="개인정보보호 전문가",
        description="개인정보 보호 정책·인증(ISMS-P 등)·법규 준수·점검을 맡는 직무",
    ),
    MarketRole(
        role_id="backend_developer",
        label="백엔드 개발자",
        description="서버 애플리케이션·API·데이터 저장 계층을 설계하고 개발하는 직무",
    ),
    MarketRole(
        role_id="frontend_developer",
        label="프런트엔드 개발자",
        description="웹 화면과 사용자 상호작용을 설계하고 개발하는 직무",
    ),
    MarketRole(
        role_id="infra_engineer",
        label="인프라 개발자",
        description="클라우드·서버·배포 자동화·모니터링 등 서비스 인프라를 만들고 운영하는 직무",
    ),
]
ROLE_LABELS = {role.role_id: role.label for role in ROLES}


@dataclass(frozen=True)
class _Skill:
    skill_id: str
    label: str
    category: str
    aliases: tuple[str, ...]  # 문장 안에서 찾는 표기 (앞뒤가 영문·숫자가 아닐 때만 일치)
    stack_only: tuple[str, ...] = ()  # 기술스택 항목과 정확히 같을 때만 인정하는 짧은 표기 (예: Go, R, C)
    regex: tuple[str, ...] = field(default=())  # 정규화된 텍스트용 추가 패턴 (한글 복합어 구분 등)


def _s(skill_id, label, category, aliases, stack_only=(), regex=()):
    return _Skill(skill_id, label, category, tuple(aliases), tuple(stack_only), tuple(regex))


_SKILLS = [
    # --- 언어 ---
    _s("java", "Java", "language", ["Java"], regex=[r"자바(?!\s*스크립트)"]),
    _s("kotlin", "Kotlin", "language", ["Kotlin", "코틀린"]),
    _s("python", "Python", "language", ["Python", "Python3", "파이썬"]),
    _s("javascript", "JavaScript", "language", ["JavaScript", "자바스크립트", "ES6"], stack_only=["JS"]),
    _s("typescript", "TypeScript", "language", ["TypeScript", "타입스크립트"], stack_only=["TS"]),
    _s("go", "Go", "language", ["Golang", "Go언어", "Go 언어"], stack_only=["Go"]),
    _s("scala", "Scala", "language", ["Scala", "스칼라"]),
    _s("csharp", "C#", "language", ["C#", ".NET", "ASP.NET"]),
    _s("cpp", "C/C++", "language", ["C++", "C/C++"], stack_only=["C"]),
    _s("php", "PHP", "language", ["PHP"]),
    _s("ruby", "Ruby", "language", ["Ruby", "Ruby on Rails", "Rails"]),
    _s("rust", "Rust", "language", ["Rust"]),
    _s("r_lang", "R", "language", ["R언어", "R 언어", "R Studio", "RStudio"], stack_only=["R"]),
    _s("sql", "SQL", "language", ["SQL"]),
    _s("shell", "Shell Script", "language", ["Shell Script", "Shell", "Bash", "쉘 스크립트", "쉘스크립트", "셸 스크립트"]),
    _s("html_css", "HTML/CSS", "language", ["HTML", "HTML5", "CSS", "CSS3"]),
    # --- 백엔드 프레임워크 ---
    _s("spring", "Spring", "framework", ["Spring", "Spring Framework", "스프링"]),
    _s("spring_boot", "Spring Boot", "framework", ["Spring Boot", "SpringBoot", "스프링 부트", "스프링부트"]),
    _s("jpa", "JPA/Hibernate", "framework", ["JPA", "Hibernate", "Spring Data JPA", "QueryDSL"]),
    _s("mybatis", "MyBatis", "framework", ["MyBatis", "iBatis", "마이바티스"]),
    _s("nodejs", "Node.js", "framework", ["Node.js", "NodeJS", "Node js", "Express", "Express.js"]),
    _s("nestjs", "NestJS", "framework", ["NestJS", "Nest.js"]),
    _s("django", "Django", "framework", ["Django", "장고"]),
    _s("fastapi", "FastAPI", "framework", ["FastAPI", "Flask"]),
    _s("laravel", "Laravel", "framework", ["Laravel", "CodeIgniter"]),
    # --- 프런트엔드 ---
    _s("react", "React", "framework", ["React", "React.js", "ReactJS", "리액트"]),
    _s("vue", "Vue.js", "framework", ["Vue", "Vue.js", "VueJS", "Vue3", "뷰js"]),
    _s("angular", "Angular", "framework", ["Angular", "AngularJS"]),
    _s("svelte", "Svelte", "framework", ["Svelte", "SvelteKit"]),
    _s("nextjs", "Next.js", "framework", ["Next.js", "NextJS", "Next js"]),
    _s("nuxt", "Nuxt", "framework", ["Nuxt", "Nuxt.js", "NuxtJS"]),
    _s("state_management", "상태 관리 라이브러리", "framework",
       ["Redux", "Redux Toolkit", "Zustand", "Recoil", "MobX", "Jotai", "Pinia", "Vuex", "상태 관리", "상태관리"]),
    _s("react_query", "React Query", "framework", ["React Query", "TanStack Query", "react-query", "SWR"]),
    _s("css_tooling", "CSS 도구", "framework",
       ["Tailwind", "TailwindCSS", "Tailwind CSS", "styled-components", "Sass", "SCSS", "CSS-in-JS"]),
    _s("bundler", "빌드 도구", "framework", ["Webpack", "Vite", "Babel", "Rollup", "esbuild", "Turborepo", "번들러"]),
    _s("frontend_test", "프런트엔드 테스트", "practice",
       ["Jest", "Vitest", "Cypress", "Playwright", "Testing Library", "Storybook"]),
    _s("web_standards", "웹 표준·접근성", "practice",
       ["웹 표준", "웹표준", "웹 접근성", "웹접근성", "크로스 브라우징", "크로스브라우징", "반응형", "SEO"]),
    _s("rendering", "SSR/CSR 렌더링", "practice", ["SSR", "CSR", "SSG", "서버 사이드 렌더링", "서버사이드 렌더링"]),
    _s("performance", "성능 최적화", "practice",
       ["성능 최적화", "성능 개선", "성능 튜닝", "웹 성능", "렌더링 최적화", "Core Web Vitals", "Lighthouse"]),
    _s("design_collab", "디자인 협업 도구", "practice", ["Figma", "피그마", "Zeplin", "디자인 시스템"]),
    _s("mobile_cross", "크로스플랫폼 앱", "framework", ["React Native", "Flutter", "리액트 네이티브"]),
    # --- 데이터베이스·저장소 ---
    _s("mysql", "MySQL/MariaDB", "database", ["MySQL", "MariaDB"]),
    _s("postgresql", "PostgreSQL", "database", ["PostgreSQL", "Postgres", "PostgresQL"]),
    _s("oracle", "Oracle", "database", ["Oracle", "오라클", "PL/SQL"]),
    _s("mssql", "MS SQL Server", "database", ["MSSQL", "MS-SQL", "MS SQL", "SQL Server"]),
    _s("rdbms", "RDBMS", "database", ["RDBMS", "RDB", "관계형 데이터베이스", "관계형 DB"]),
    _s("mongodb", "MongoDB", "database", ["MongoDB", "몽고DB"]),
    _s("redis", "Redis", "database", ["Redis", "레디스"]),
    _s("nosql", "NoSQL", "database", ["NoSQL", "DynamoDB", "Cassandra", "HBase"]),
    _s("elasticsearch", "Elasticsearch", "database", ["Elasticsearch", "ElasticSearch", "OpenSearch", "엘라스틱서치"]),
    # --- 데이터 처리 ---
    _s("spark", "Apache Spark", "data", ["Apache Spark", "Spark", "PySpark", "스파크"]),
    _s("hadoop", "Hadoop", "data", ["Hadoop", "HDFS", "하둡", "Hive", "MapReduce", "YARN"]),
    _s("kafka", "Apache Kafka", "data", ["Apache Kafka", "Kafka", "카프카"]),
    _s("flink", "Apache Flink", "data", ["Apache Flink", "Flink"]),
    _s("airflow", "Apache Airflow", "data", ["Apache Airflow", "Airflow", "에어플로우"]),
    _s("etl", "ETL/데이터 파이프라인", "data",
       ["ETL", "ELT", "데이터 파이프라인", "데이터파이프라인", "Data Pipeline"]),
    _s("data_warehouse", "데이터 웨어하우스", "data",
       ["데이터 웨어하우스", "데이터웨어하우스", "Data Warehouse", "DW", "BigQuery", "Redshift", "Snowflake",
        "데이터 레이크", "데이터레이크", "Data Lake", "Lakehouse"]),
    _s("databricks", "Databricks/dbt", "data", ["Databricks", "dbt", "Delta Lake", "Iceberg"]),
    _s("query_engine", "분산 쿼리 엔진", "data", ["Presto", "Trino", "Impala", "Athena"]),
    _s("data_modeling", "데이터 모델링", "data", ["데이터 모델링", "데이터모델링", "data modeling"]),
    _s("bi_tools", "BI·시각화 도구", "data", ["Tableau", "태블로", "Power BI", "Looker", "Superset", "Redash"]),
    _s("machine_learning", "머신러닝", "data",
       ["머신러닝", "Machine Learning", "딥러닝", "Deep Learning", "PyTorch", "TensorFlow", "MLOps", "LLM"],
       stack_only=["ML"]),
    _s("pandas", "Pandas/NumPy", "data", ["Pandas", "NumPy"]),
    _s("message_queue", "메시지 큐", "data", ["RabbitMQ", "ActiveMQ", "SQS", "메시지 큐", "메시지큐", "MQ"]),
    # --- 아키텍처·API ---
    _s("rest_api", "REST API", "practice", ["REST API", "RESTful", "API 설계", "API 개발"]),
    _s("graphql", "GraphQL/gRPC", "practice", ["GraphQL", "gRPC"]),
    _s("msa", "MSA", "practice", ["MSA", "마이크로서비스", "Microservice", "Microservices"]),
    _s("testing", "테스트 코드", "practice", ["TDD", "테스트 코드", "테스트코드", "단위 테스트", "단위테스트", "JUnit"]),
    _s("large_traffic", "대용량 트래픽 처리", "practice", ["대용량 트래픽", "대규모 트래픽", "대용량 데이터", "대용량 처리"]),
    _s("agile", "애자일·협업 도구", "practice", ["Agile", "애자일", "Scrum", "스크럼", "Jira", "Confluence"]),
    _s("code_review", "코드 리뷰", "practice", ["코드 리뷰", "코드리뷰", "Code Review"]),
    # --- 클라우드·인프라 ---
    _s("aws", "AWS", "cloud", ["AWS", "Amazon Web Services", "EC2", "EKS", "ECS"]),
    _s("gcp", "GCP", "cloud", ["GCP", "Google Cloud", "GKE"]),
    _s("azure", "Azure", "cloud", ["Azure", "애저"]),
    _s("ncp", "국내 클라우드(NCP 등)", "cloud",
       ["NCP", "Naver Cloud", "네이버 클라우드", "네이버클라우드", "KT Cloud", "NHN Cloud"]),
    _s("cloud_general", "클라우드 환경 경험", "cloud", ["클라우드", "Cloud"]),
    _s("on_premise", "온프레미스·가상화", "cloud",
       ["온프레미스", "On-Premise", "On Premise", "IDC", "VMware", "가상화", "OpenStack"]),
    _s("docker", "Docker", "devops", ["Docker", "도커", "컨테이너"]),
    _s("kubernetes", "Kubernetes", "devops", ["Kubernetes", "K8s", "쿠버네티스", "Helm"]),
    _s("terraform", "IaC(Terraform 등)", "devops",
       ["Terraform", "테라폼", "Ansible", "앤서블", "IaC", "CloudFormation", "Pulumi"]),
    _s("cicd", "CI/CD", "devops",
       ["CI/CD", "CICD", "Jenkins", "젠킨스", "GitHub Actions", "GitLab CI", "ArgoCD", "Argo CD", "배포 자동화"]),
    _s("git", "Git", "devops", ["Git", "GitHub", "GitLab", "Bitbucket", "깃허브"]),
    _s("linux", "Linux", "devops", ["Linux", "리눅스", "Unix", "유닉스", "CentOS", "Ubuntu", "RHEL"]),
    _s("network", "네트워크", "devops",
       ["TCP/IP", "네트워크", "Network", "DNS", "로드밸런서", "로드 밸런서", "Load Balancer", "L4", "L7", "VPN", "CDN"]),
    _s("web_server", "웹 서버·WAS", "devops", ["Nginx", "Apache HTTP", "아파치", "Tomcat", "톰캣", "WAS"],
       regex=[r"(?<![a-z0-9_])apache(?!\s+(?:spark|kafka|flink|airflow|hadoop|hive|iceberg|druid|beam))(?![a-z0-9_])"]),
    _s("monitoring", "모니터링·관측", "devops",
       ["모니터링", "Monitoring", "Prometheus", "프로메테우스", "Grafana", "그라파나", "Datadog", "ELK",
        "Kibana", "Observability", "APM", "Zabbix"]),
    _s("sre", "SRE·장애 대응", "devops", ["SRE", "장애 대응", "장애대응", "고가용성", "가용성", "On-call", "온콜"]),
    _s("devops_culture", "DevOps", "devops", ["DevOps", "데브옵스", "DevSecOps"]),
    _s("dba", "DB 운영·튜닝", "database", ["DBA", "쿼리 튜닝", "쿼리튜닝", "쿼리 최적화", "DB 튜닝", "인덱스 설계"]),
    # --- 보안 ---
    _s("security_solution", "보안 솔루션 운영", "security",
       ["방화벽", "Firewall", "WAF", "IPS", "IDS", "DLP", "DRM", "NAC", "EDR", "SIEM", "백신", "보안 솔루션", "보안솔루션"]),
    _s("vulnerability", "취약점 진단·모의해킹", "security",
       ["취약점 진단", "취약점진단", "취약점 점검", "취약점 분석", "모의해킹", "모의 해킹", "Penetration", "침투 테스트"]),
    _s("security_monitoring", "보안 관제·침해 대응", "security",
       ["보안관제", "보안 관제", "침해사고", "침해 사고", "침해대응", "침해 대응", "CERT", "포렌식"]),
    _s("access_control", "접근통제·계정 관리", "security",
       ["접근통제", "접근 통제", "접근제어", "접근 제어", "계정 관리", "계정관리", "권한 관리", "권한관리", "IAM", "SSO"]),
    _s("encryption", "암호화", "security", ["암호화", "Encryption", "KMS", "PKI", "키 관리"]),
    _s("cloud_security", "클라우드 보안", "security", ["클라우드 보안", "Cloud Security", "CSPM", "CWPP"]),
    _s("secure_coding", "시큐어 코딩·보안 검토", "security",
       ["시큐어 코딩", "시큐어코딩", "보안성 검토", "보안성검토", "보안 검토", "보안 아키텍처"]),
    _s("security_policy", "보안 정책·지침 수립", "security",
       ["보안 정책", "보안정책", "보안 지침", "정보보호 정책", "정보보호정책", "보안 규정", "정책 수립", "지침 수립"]),
    _s("security_audit", "보안 점검·감사", "security",
       ["보안 감사", "보안감사", "보안 점검", "보안점검", "내부 감사", "내부감사", "IT 감사", "실태점검", "실태 점검"]),
    _s("risk_management", "위험 평가·관리", "security",
       ["위험 평가", "위험평가", "위험 관리", "위험관리", "리스크 관리", "Risk Assessment", "위험 분석"]),
    _s("security_training", "보안 교육·인식 제고", "security", ["보안 교육", "보안교육", "정보보호 교육", "개인정보 교육", "인식 제고"]),
    # --- 개인정보보호·컴플라이언스 ---
    _s("privacy_law", "개인정보보호법", "privacy",
       ["개인정보보호법", "개인정보 보호법"],
       regex=[r"개인정보\s*(?:보호)?\s*(?:관련|관계)?\s*(?:법률|법령|법규|법제|규제)"]),
    _s("network_act", "정보통신망법·신용정보법", "privacy", ["정보통신망법", "신용정보법", "전자금융거래법", "전자금융감독규정", "위치정보법"]),
    _s("gdpr", "GDPR 등 해외 규제", "privacy", ["GDPR", "CCPA", "CPRA", "해외 개인정보", "국외 이전", "국외이전"]),
    _s("isms_p", "ISMS/ISMS-P 인증", "privacy", ["ISMS-P", "ISMS", "PIMS"]),
    _s("iso27001", "ISO 27001/27701", "privacy",
       ["ISO 27001", "ISO27001", "ISO/IEC 27001", "ISO 27701", "ISO27701", "ISO 27017", "ISO 27018", "CSA STAR"]),
    _s("pia", "개인정보 영향평가(PIA)", "privacy", ["개인정보 영향평가", "개인정보영향평가", "영향평가", "PIA"]),
    _s("privacy_policy", "개인정보 처리방침·동의 관리", "privacy",
       ["개인정보 처리방침", "개인정보처리방침", "처리방침", "동의서", "수집·이용 동의", "수집 이용 동의", "개인정보 동의"]),
    _s("privacy_lifecycle", "개인정보 처리 흐름·파기 관리", "privacy",
       ["개인정보 흐름", "개인정보 처리 흐름", "개인정보 흐름도", "개인정보 파기", "파기 관리", "개인정보 처리 현황",
        "개인정보 라이프사이클", "개인정보 생명주기"]),
    _s("privacy_outsourcing", "수탁사·위탁 관리", "privacy",
       ["수탁사", "수탁자", "위탁 관리", "위수탁", "위탁업체", "협력사 보안", "제3자 제공"]),
    _s("privacy_breach", "개인정보 유출·침해 대응", "privacy",
       ["개인정보 유출", "유출 사고", "유출사고", "개인정보 침해", "개인정보 사고"]),
    _s("pseudonymization", "가명·익명 처리", "privacy", ["가명처리", "가명 처리", "가명정보", "익명처리", "익명 처리", "비식별"]),
    _s("privacy_tech", "개인정보 보호 기술 조치", "privacy",
       ["개인정보 암호화", "접속기록", "접속 기록", "안전성 확보조치", "안전성 확보 조치", "기술적 보호조치",
        "기술적·관리적 보호조치", "관리적 보호조치", "개인정보 마스킹", "마스킹"]),
    _s("compliance", "컴플라이언스·규제 대응", "privacy",
       ["컴플라이언스", "Compliance", "규제 대응", "법규 준수", "대관 업무", "대관업무", "감독기관", "개인정보보호위원회", "금융보안원", "KISA"]),
    _s("privacy_officer", "CPO·개인정보 보호 조직 운영", "privacy",
       ["CPO", "개인정보 보호책임자", "개인정보보호책임자", "CISO", "정보보호 최고책임자", "개인정보보호 업무", "개인정보 보호 업무"]),
    _s("mydata", "마이데이터", "privacy", ["마이데이터", "MyData", "본인신용정보"]),
    _s("privacy_consulting", "개인정보·보안 컨설팅", "privacy",
       ["개인정보보호 컨설팅", "개인정보 컨설팅", "정보보호 컨설팅", "보안 컨설팅", "보안컨설팅", "인증 컨설팅"]),
    # --- 자격증 ---
    _s("cert_cppg", "CPPG", "certification", ["CPPG", "개인정보관리사"]),
    _s("cert_infosec", "정보보안기사", "certification", ["정보보안기사", "정보보안산업기사", "정보보호전문가"]),
    _s("cert_cissp", "CISSP/CISA/CISM", "certification", ["CISSP", "CISA", "CISM"]),
    _s("cert_isms_auditor", "ISMS-P 인증심사원", "certification", ["인증심사원", "인증 심사원", "ISO 27001 심사원", "선임심사원"]),
    _s("cert_pia", "PIA 전문인력", "certification", ["PIA 전문인력", "영향평가 전문인력", "PIA 자격", "PIA자격"]),
    _s("cert_info_processing", "정보처리기사", "certification", ["정보처리기사", "정보처리산업기사"]),
    _s("cert_cloud", "클라우드 자격증", "certification",
       ["AWS 자격증", "AWS Certified", "CKA", "CKAD", "Solutions Architect", "클라우드 자격증"]),
    _s("cert_data", "데이터 자격증", "certification", ["SQLD", "SQLP", "ADsP", "ADP", "빅데이터분석기사"]),
]


def _alias_pattern(alias: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9_])" + re.escape(normalized_text(alias)) + r"(?![a-z0-9_])")


SKILL_IDS = [skill.skill_id for skill in _SKILLS]
if len(SKILL_IDS) != len(set(SKILL_IDS)):
    raise RuntimeError("Duplicate skill_id in the market catalog")

SKILL_BY_ID = {skill.skill_id: skill for skill in _SKILLS}
# 정규화된 문장에서 찾는 패턴 (긴 표기 먼저 볼 필요는 없다: 공고당 기술별로 '있다/없다'만 센다)
TEXT_PATTERNS = {
    skill.skill_id: [_alias_pattern(alias) for alias in skill.aliases] + [re.compile(p) for p in skill.regex]
    for skill in _SKILLS
}
# 기술스택 항목 하나가 통째로 이 표기와 같으면 그 기술이다 (Go, R, C 같은 짧은 이름)
STACK_EXACT = {
    normalized_text(alias): skill.skill_id
    for skill in _SKILLS
    for alias in (*skill.aliases, *skill.stack_only)
}

CATALOG = MarketCatalog(
    version=CATALOG_VERSION,
    roles=ROLES,
    skills=[
        MarketSkill(skill_id=s.skill_id, label=s.label, category=s.category, aliases=list(s.aliases)[:20])
        for s in _SKILLS
    ],
)
