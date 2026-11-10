"""DB 스키마 마이그레이션 실행기 (로컬 Compose·CI 테스트·AWS RDS 공통).

    python -m app.migrate           # 빈 DB면 기본 스키마부터, 아니면 아직 적용하지 않은 마이그레이션만 적용
    python -m app.migrate --seed    # 빈 DB를 처음 만들 때만 시드(테스트 계정)도 넣는다. 운영(RDS)에서는 쓰지 않는다.

- 접속 정보는 앱과 같은 DB_HOST·DB_PORT·DB_USER·DB_PASSWORD·DB_NAME 환경변수다 (PGSSLMODE 등 libpq 변수도 그대로 적용).
  APP_ENV=production(운영 이미지 기본값)에서 DB_PASSWORD가 비었거나 로컬 기본값이면 접속하지 않고 실패한다 (종료 코드 1).
- 원장 public.schema_migrations(filename, checksum, applied_at)에 적용한 DB/migrations 파일을 남기고 다시 실행하지 않는다.
  적용한 뒤 SQL이 바뀐 파일이 있으면 아무것도 적용하지 않고 실패한다 (CLI 종료 코드 1). 바꿀 내용은 다음 번호 파일로 추가한다.
- 빈 DB(public.users 없음): DB/schema의 기본 스키마 두 개(--seed면 DB/seeds/03_seed_data.sql까지)와 원장을 한 트랜잭션으로 만든다.
- 원장 없이 이미 만들어진 DB(docker-compose initdb·수동 psql): 마이그레이션을 모두 다시 적용하고 기록한다.
  그래서 마이그레이션은 여러 번 실행해도 안전하게 쓴다 (IF NOT EXISTS 등). 새 Compose 볼륨은 initdb가 같은 파일을 먼저 실행한다.
- DB/migrations/NN_*.sql은 번호 순으로, 파일마다 새 연결(시드 덤프가 search_path를 비우므로)의 한 트랜잭션에서 원장 기록과
  함께 적용한다. 실패하면 그 파일과 원장 기록이 함께 롤백된다. psql 메타 명령 줄(\\로 시작)은 서버가 모르므로 빼고 보낸다.
- 여러 개가 동시에 실행돼도(ECS 태스크 여러 개 등) advisory lock으로 하나씩 실행한다.
- 운영(APP_ENV=production, BE 이미지 기본값. 앱과 같이 읽고 비우면 development, development가 아니면 운영으로 본다)이면
  --seed를 거부하고, 적용을 마친 뒤 공개된 시드 테스트 비밀번호(04 마이그레이션의 해시)를 쓰는 계정이 있으면 실패한다
  (시드·개발 DB 데이터가 운영 DB에 들어간 경우).
- DB_APP_ROLE(선택, 운영 RDS): BE 서비스가 접속하는 별도 계정. 주면 실행이 끝날 때마다(advisory lock 안에서) 그 계정의
  public 테이블·시퀀스 권한을 모두 거둔 뒤 앱 테이블 DML과 시퀀스 사용 권한만 다시 준다(새 테이블 포함, 원장 제외. 예전에 그
  계정에 준 TRUNCATE 등은 남지 않는다. TRIGGER·REFERENCES·원장 쓰기 권한은 아래 검사가 거부한다). 그 계정 검사는 lock을 잡은
  직후(기본 스키마·마이그레이션 SQL보다 먼저)와 권한을 주기 직전에 하고, 걸리면 아무것도 적용하거나 주지 않고 실패한다.
  아래에서 "속한 역할"은 권한을 상속하지 않는 소속(NOINHERIT, GRANT ... WITH INHERIT FALSE)도 포함한다 (SET ROLE로 그 역할이 될 수 있다).
  - 없는 역할이거나 특권 계정(그 역할이나 속한 역할의 슈퍼유저·CREATEROLE·CREATEDB·REPLICATION·BYPASSRLS 속성, 마이그레이션
    계정 자신이나 그 소속, rds_superuser 같은 기본 제공 특권 역할 소속)이면 실패한다. 특권 속성이 있는 소속 역할은 이름을 알려 준다.
  - 그 역할이나 속한 역할이 public 스키마나 DB에 CREATE 권한이 있거나(PostgreSQL 14 이하에서 만들었거나 업그레이드한 DB의
    기본값, PUBLIC에 준 DB CREATE 등) DB나 public 스키마를 소유하면(소유자는 거둔 CREATE를 스스로 다시 줄 수 있다. PostgreSQL 15
    이상의 새 DB에서는 DB 소유자가 pg_database_owner로 public 스키마도 소유한다) 실패하고, 걸린 검사와 그 권한·소유를 가진 소속
    역할, 나온 순서대로 실행할 REVOKE·ALTER ... OWNER 문을 알려 준다. 지금 소유자 몫의 CREATE를 이미 거뒀으면 새 소유자에게
    CREATE를 다시 주는 GRANT 문을, 마이그레이션 계정이 슈퍼유저가 아니면(RDS 마스터 사용자) 먼저 지금 소유자의 권한을 받는 GRANT
    문을 함께 알려 준다. 소속을 거두는 방법은 알려 주지 않는다(이 문들은 소속을 그대로 두므로 다음 실행에서 그 소속 역할이 public에
    만들어 둔 객체가 아래 소유 객체 검사에 걸린다).
  - 그 역할이나 속한 역할, 기본 제공 역할(pg_database_owner·pg_monitor 등. pg_database_owner의 것은 DB 소유자를 바꾸면 새 소유자의
    것이 된다), public 스키마에 CREATE 권한이 있거나 DB·public 스키마를 소유한 다른 역할(부트스트랩 슈퍼유저·마이그레이션 계정
    제외. 소속을 거둔 예전 소속 역할 등)이 public 스키마에 객체(함수·연산자·타입·테이블·확장 등)를 소유해도 실패하고 그 객체를
    10개까지 알려 준다 (CREATE를 거두기 전에 만든 객체도 남아 마이그레이션 계정·앱이 search_path로 찾아 실행할 수 있다).
  - 그 역할이나 속한 역할이 public 스키마의 테이블에 TRIGGER 권한이 있거나(그 권한으로 이미 만든 트리거는 REVOKE 뒤에도 남으므로
    public 스키마 테이블의 트리거도 알려 준다), public 스키마 테이블의 트리거에 WHEN 조건이 있거나 그 트리거가 임시 스키마의
    함수나 위 역할이 소유한 함수를 부르면 실패한다 (트리거 함수와 WHEN 조건은 그 테이블을 바꾸는 마이그레이션 계정의 권한으로
    실행된다).
  - 그 역할이나 속한 역할(또는 PUBLIC)이 public 스키마의 테이블에 REFERENCES 권한이 있거나(열 단위 포함. 외래 키 검사는 참조되는
    테이블 소유자의 권한으로 실행되어 그 역할이 만든 암시적 캐스트 같은 함수를 마이그레이션 계정의 권한으로 실행하게 할 수 있다.
    그 권한으로 이미 만든 외래 키는 REVOKE 뒤에도 남으므로 public 밖에서 public 테이블을 참조하는 외래 키도 알려 준다), 원장
    public.schema_migrations에 INSERT·UPDATE·DELETE·TRUNCATE 권한이 있으면 실패하고, 그 권한을 받은 역할마다 REVOKE 문을 알려
    준다 (실행이 끝날 때 거두는 것은 그 역할 자신의 권한뿐이다).
  - 그 역할이나 속한 역할이 이 DB의 스키마를 소유해도(예전 DB CREATE 권한으로 만든 것 등. 타입·캐스트·함수를 둘 수 있고 대화형
    세션의 "$user" 스키마가 된다) 실패하고 그 스키마를 알려 준다 (안을 확인한 뒤 DROP SCHEMA ... CASCADE나 ALTER SCHEMA ... OWNER).
- 실행기 연결의 search_path는 고정한다(DB·역할에 건 search_path 설정과 마이그레이션 계정 이름의 스키마 "$user"를 쓰지 않는다).
  기본 스키마·마이그레이션 파일 연결은 public(pg_catalog는 늘 먼저 찾는다), 실행기 자신의 SQL(서비스 계정 검사·원장 읽기·시드
  해시 확인·GRANT)을 실행하는 lock 연결은 pg_catalog만 찾는다(public에 심은 함수·연산자도 부르지 않는다). 이 파일의 SQL은
  카탈로그 함수·테이블에 pg_catalog.를 붙인다.
"""
import argparse
import hashlib
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg import sql
from psycopg.rows import tuple_row

from app.services.database import get_db_connection

logger = logging.getLogger("app.migrate")

DB_DIR = Path(__file__).resolve().parents[1] / "DB"
# 빈 DB에 마이그레이션보다 먼저 넣는 파일. 기본 스키마에는 04~07 내용이 이미 들어 있다.
BASELINE = ("schema/schema_PostgreSQL.sql", "schema/schema_pgvector.sql")
SEED = "seeds/03_seed_data.sql"  # 로컬·테스트 전용 (테스트 계정). 운영 이미지에는 넣지 않는다.
# 04 마이그레이션이 시드 테스트 계정(user001~user045@example.com)에 넣는 bcrypt 해시 (평문 비밀번호는 README에 공개).
# 운영 DB에 이 해시를 쓰는 계정이 있으면 누구나 로그인할 수 있으므로 운영 마이그레이션을 실패시킨다 (값은 로그에 남기지 않는다).
# 비밀번호가 아니라 해시 값 그대로 비교하므로 사용자가 가입으로 이 값을 만들 수 없다 (tests/test_migrate.py가 04와 같은지 검사).
SEED_PASSWORD_HASH = "$2b$12$NE/ahavIaowONbReQO8HeerLjtjlupCA.UjiFQM7Aw6YvIveWlYSS"
_NAME = re.compile(r"^(\d{2,})_[A-Za-z0-9_]+\.sql$")
# 이 앱의 마이그레이션 전용 advisory lock 번호 (임의 상수). 잠금은 DB마다 따로다.
_LOCK_KEY = 7_365_617_102_838_401
_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    filename    TEXT PRIMARY KEY,
    checksum    CHAR(64) NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT pg_catalog.now()
)"""
# BE 서비스 계정(DB_APP_ROLE)에 주는 권한: 앱 테이블 DML과 시퀀스 사용만. 스키마 변경·역할 관리·원장은 마이그레이션 계정만 한다.
# 예전에 그 계정에 따로 준 권한(GRANT ALL의 TRUNCATE 등)이 남지 않게 public 테이블·시퀀스의 권한을 먼저 모두 거둔다.
# 이 문들은 그 계정 자신의 권한만 바꾸므로 TRIGGER·REFERENCES·원장 쓰기 권한은 속한 역할·PUBLIC의 것까지 check_app_role이 먼저 거부한다.
_APP_ROLE_GRANTS = (
    "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}",
    "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}",
    "GRANT CONNECT ON DATABASE {db} TO {role}",
    "GRANT USAGE ON SCHEMA public TO {role}",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}",
    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}",
    "REVOKE ALL ON public.schema_migrations FROM {role}",
)
# 서비스 계정이 가지면 안 되는 것: 슈퍼유저·역할/DB 생성·복제·RLS 우회(그 역할이 속한 역할의 속성도 본다. 권한을 상속하지
# 않는 소속도 SET ROLE로 그 역할이 될 수 있다), 마이그레이션 계정(테이블 소유자) 자신이거나 그 소속,
# rds_superuser·서버 파일/프로그램 접근·전체 데이터 읽기/쓰기 같은 기본 제공 특권 역할 소속.
# 두 번째 값은 특권 속성이 있는 소속 역할(그 역할 자신 제외)의 이름이다 (메시지용).
_PRIVILEGED_ROLE = """
SELECT r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls
       OR pg_catalog.cardinality(a.members) > 0
       OR pg_catalog.pg_has_role(r.oid, current_user, 'MEMBER')
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_roles s
                  WHERE s.rolname IN ('rds_superuser', 'pg_read_server_files', 'pg_write_server_files',
                                      'pg_execute_server_program', 'pg_read_all_data', 'pg_write_all_data')
                    AND pg_catalog.pg_has_role(r.oid, s.oid, 'MEMBER')),
       a.members
FROM pg_catalog.pg_roles r,
     LATERAL (SELECT ARRAY(SELECT m.rolname FROM pg_catalog.pg_roles m
                           WHERE m.oid <> r.oid AND pg_catalog.pg_has_role(r.oid, m.oid, 'MEMBER')
                             AND (m.rolsuper OR m.rolcreaterole OR m.rolcreatedb OR m.rolreplication OR m.rolbypassrls)
                           ORDER BY m.rolname) AS members) a
WHERE r.rolname = %s"""
# 서비스 계정이 public 스키마나 이 DB에 객체를 만들 수 있어도 안 된다: 서비스 계정이 만든 함수·연산자(예: public.lower)나
# 마이그레이션 계정 이름의 스키마를 마이그레이션 계정·앱이 search_path로 찾아 실행하게 된다 (CVE-2018-1058).
# PostgreSQL 14 이하는 PUBLIC에 public 스키마 CREATE를 기본으로 주고, 그 버전에서 업그레이드·복원한 DB에도 남는다.
# 그 역할 자신뿐 아니라 그 역할이 속한 모든 역할(MEMBER)의 CREATE도 본다: 권한을 상속하지 않는 소속(NOINHERIT 역할,
# PostgreSQL 16의 GRANT ... WITH INHERIT FALSE)도 SET ROLE로 그 역할의 CREATE를 쓸 수 있다. SET ROLE까지 막은 소속도 넓게 거부한다.
# 소유도 만들 수 있는 것으로 본다: public 스키마나 DB의 소유자는 자기 CREATE를 거둬도 스스로 다시 줄 수 있다.
# DB 소유자는 pg_database_owner(PostgreSQL 15부터 public 스키마의 소유자)에 속하므로 public 스키마도 소유한다.
_CAN_CREATE = """
SELECT pg_catalog.bool_or(pg_catalog.has_schema_privilege(m.oid, 'public', 'CREATE') OR m.oid = n.nspowner),
       pg_catalog.bool_or(pg_catalog.has_database_privilege(m.oid, pg_catalog.current_database(), 'CREATE')
                          OR m.oid = d.datdba)
FROM pg_catalog.pg_roles r JOIN pg_catalog.pg_roles m ON pg_catalog.pg_has_role(r.oid, m.oid, 'MEMBER')
CROSS JOIN pg_catalog.pg_namespace n CROSS JOIN pg_catalog.pg_database d
WHERE r.rolname = %s AND n.nspname = 'public' AND d.datname = pg_catalog.current_database()"""
# 거부 메시지용: 그 역할이 속한 역할(그 역할 자신 포함) 중 public 스키마·DB의 소유자와, ACL에서 CREATE를 직접 받은 역할
# (소유자 몫 제외. 이름이 NULL이면 PUBLIC). 넷째 값은 소유자 몫의 ACL에 CREATE가 있는지다
# (거둔 채로 소유자를 바꾸면 새 소유자도 CREATE를 받지 못한다. ACL이 비어 있으면 기본값이라 소유자에게 CREATE가 있다).
_CREATE_HOLDERS = """
SELECT x.kind, m.rolname, x.holder = r.oid, x.owner_create
FROM pg_catalog.pg_roles r CROSS JOIN pg_catalog.pg_namespace n CROSS JOIN pg_catalog.pg_database d
CROSS JOIN LATERAL (SELECT 'schema owner', n.nspowner, EXISTS (
                      SELECT 1 FROM pg_catalog.aclexplode(COALESCE(n.nspacl, pg_catalog.acldefault('n', n.nspowner))) a
                      WHERE a.grantee = n.nspowner AND a.privilege_type = 'CREATE')
                    UNION ALL SELECT 'db owner', d.datdba, EXISTS (
                      SELECT 1 FROM pg_catalog.aclexplode(COALESCE(d.datacl, pg_catalog.acldefault('d', d.datdba))) a
                      WHERE a.grantee = d.datdba AND a.privilege_type = 'CREATE')
                    UNION ALL SELECT 'schema', a.grantee, NULL FROM pg_catalog.aclexplode(n.nspacl) a
                              WHERE a.privilege_type = 'CREATE' AND a.grantee <> n.nspowner
                    UNION ALL SELECT 'db', a.grantee, NULL FROM pg_catalog.aclexplode(d.datacl) a
                              WHERE a.privilege_type = 'CREATE' AND a.grantee <> d.datdba
                   ) AS x (kind, holder, owner_create)
LEFT JOIN pg_catalog.pg_roles m ON m.oid = x.holder
WHERE r.rolname = %s AND n.nspname = 'public' AND d.datname = pg_catalog.current_database()
  AND (m.oid IS NULL OR pg_catalog.pg_has_role(r.oid, m.oid, 'MEMBER'))
ORDER BY 1, 2"""
# 서비스 계정이나 그 역할이 속한 역할, 기본 제공 역할(OID 16384 미만. 부트스트랩 슈퍼유저 10 제외)이 public 스키마에 소유한
# 객체(함수·연산자·타입·테이블·확장 등). CREATE를 거둬도 그 전에 만든 객체는 남고, 마이그레이션 계정·앱이 search_path(public)로
# 찾아 부르는 계정의 권한으로 실행하게 된다. 기본 제공 역할은 소유 기록(pg_shdepend)이 남지 않고, 서비스 계정이 DB 소유자일 때
# pg_database_owner에게 준 객체는 DB 소유자를 바꾸면 마이그레이션 계정의 것이 되므로 카탈로그마다 소유자 열을 본다. 인덱스·TOAST,
# 다른 객체에 딸린(내부 의존) 행 타입·배열 타입, 복합 타입의 pg_class 행은 그 객체와 소유자가 같아 빼고, 고른 뒤 이름을 붙인다.
# 다른 스키마·임시 스키마의 객체는 실행기가 고정한 search_path에 없으므로 보지 않는다(트리거로 부르는 함수는 _UNSAFE_TRIGGERS).
# 지금 public 스키마에 CREATE 권한이 있거나 DB·public 스키마를 소유한 역할(부트스트랩 슈퍼유저·마이그레이션 계정 제외)의 객체도
# 본다: 서비스 계정이 그 역할로 SET ROLE해 심은 뒤 소속만 거두면 그 역할은 더 이상 "속한 역할"이 아니지만 객체는 남는다.
_OWNED_IN_PUBLIC = """
WITH owned AS MATERIALIZED (
    SELECT x.classid, x.objid
    FROM pg_catalog.pg_roles r CROSS JOIN pg_catalog.pg_namespace n CROSS JOIN pg_catalog.pg_database d
    CROSS JOIN LATERAL (
        SELECT 'pg_catalog.pg_class'::pg_catalog.regclass::pg_catalog.oid, c.oid, c.relowner FROM pg_catalog.pg_class c
        WHERE c.relnamespace = n.oid AND c.relkind NOT IN ('i', 'I', 't', 'c')
        UNION ALL SELECT 'pg_catalog.pg_proc'::pg_catalog.regclass::pg_catalog.oid, p.oid, p.proowner
        FROM pg_catalog.pg_proc p WHERE p.pronamespace = n.oid
        UNION ALL SELECT 'pg_catalog.pg_type'::pg_catalog.regclass::pg_catalog.oid, t.oid, t.typowner
        FROM pg_catalog.pg_type t WHERE t.typnamespace = n.oid AND NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_depend e
            WHERE e.classid = 'pg_catalog.pg_type'::pg_catalog.regclass::pg_catalog.oid AND e.objid = t.oid
              AND e.deptype = 'i')
        UNION ALL SELECT 'pg_catalog.pg_operator'::pg_catalog.regclass::pg_catalog.oid, o.oid, o.oprowner
        FROM pg_catalog.pg_operator o WHERE o.oprnamespace = n.oid
        UNION ALL SELECT 'pg_catalog.pg_collation'::pg_catalog.regclass::pg_catalog.oid, o.oid, o.collowner
        FROM pg_catalog.pg_collation o WHERE o.collnamespace = n.oid
        UNION ALL SELECT 'pg_catalog.pg_conversion'::pg_catalog.regclass::pg_catalog.oid, o.oid, o.conowner
        FROM pg_catalog.pg_conversion o WHERE o.connamespace = n.oid
        UNION ALL SELECT 'pg_catalog.pg_opclass'::pg_catalog.regclass::pg_catalog.oid, o.oid, o.opcowner
        FROM pg_catalog.pg_opclass o WHERE o.opcnamespace = n.oid
        UNION ALL SELECT 'pg_catalog.pg_opfamily'::pg_catalog.regclass::pg_catalog.oid, o.oid, o.opfowner
        FROM pg_catalog.pg_opfamily o WHERE o.opfnamespace = n.oid
        UNION ALL SELECT 'pg_catalog.pg_ts_config'::pg_catalog.regclass::pg_catalog.oid, o.oid, o.cfgowner
        FROM pg_catalog.pg_ts_config o WHERE o.cfgnamespace = n.oid
        UNION ALL SELECT 'pg_catalog.pg_ts_dict'::pg_catalog.regclass::pg_catalog.oid, o.oid, o.dictowner
        FROM pg_catalog.pg_ts_dict o WHERE o.dictnamespace = n.oid
        UNION ALL SELECT 'pg_catalog.pg_statistic_ext'::pg_catalog.regclass::pg_catalog.oid, o.oid, o.stxowner
        FROM pg_catalog.pg_statistic_ext o WHERE o.stxnamespace = n.oid
        UNION ALL SELECT 'pg_catalog.pg_extension'::pg_catalog.regclass::pg_catalog.oid, o.oid, o.extowner
        FROM pg_catalog.pg_extension o WHERE o.extnamespace = n.oid) AS x (classid, objid, owner)
    WHERE r.rolname = %s AND n.nspname = 'public' AND d.datname = pg_catalog.current_database()
      AND (pg_catalog.pg_has_role(r.oid, x.owner, 'MEMBER')
           OR (x.owner < 16384::pg_catalog.oid AND x.owner <> 10::pg_catalog.oid)
           OR (x.owner <> 10::pg_catalog.oid
               AND x.owner <> (SELECT u.oid FROM pg_catalog.pg_roles u WHERE u.rolname = current_user)
               AND (pg_catalog.has_schema_privilege(x.owner, n.oid, 'CREATE') OR x.owner = n.nspowner
                    OR x.owner = d.datdba))))
SELECT o.type, o.identity, pg_catalog.count(*) OVER ()
FROM owned, LATERAL pg_catalog.pg_identify_object(owned.classid, owned.objid, 0) o
ORDER BY 1, 2 LIMIT 10"""
# 서비스 계정이나 그 역할이 속한 역할이 public 스키마의 테이블(뷰·외부 테이블 포함)에 TRIGGER 권한이 있는지(PUBLIC에 준 것·소유
# 포함). 있으면 마이그레이션이 바꾸는 테이블에 트리거를 걸어 임시 스키마(pg_temp, 기본으로 누구나 만들 수 있음)의 함수 등을
# 마이그레이션 계정의 권한으로 실행하게 할 수 있다. 두 번째 값은 거부 메시지용: ACL에서 TRIGGER를 받은 그 역할들(NULL이면 PUBLIC).
_CAN_TRIGGER = """
SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles m CROSS JOIN pg_catalog.pg_class c
               WHERE pg_catalog.pg_has_role(r.oid, m.oid, 'MEMBER') AND c.relnamespace = n.oid
                 AND c.relkind IN ('r', 'p', 'v', 'f') AND pg_catalog.has_table_privilege(m.oid, c.oid, 'TRIGGER')),
       ARRAY(SELECT DISTINCT m.rolname
             FROM pg_catalog.pg_class c CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) a
             LEFT JOIN pg_catalog.pg_roles m ON m.oid = a.grantee
             WHERE c.relnamespace = n.oid AND a.privilege_type = 'TRIGGER'
               AND (m.oid IS NULL OR pg_catalog.pg_has_role(r.oid, m.oid, 'MEMBER'))
             ORDER BY 1)
FROM pg_catalog.pg_roles r CROSS JOIN pg_catalog.pg_namespace n
WHERE r.rolname = %s AND n.nspname = 'public'"""
# public 스키마 테이블의 트리거(시스템이 만든 내부 트리거 제외) 중 WHEN 조건이 있거나 임시 스키마의 함수나 _OWNED_IN_PUBLIC과 같은
# 역할(서비스 계정·그 역할이 속한 역할·기본 제공 역할·public에 객체를 만들 수 있는 다른 역할)이 소유한 함수를 부르는 것. 트리거
# 함수와 WHEN 조건은 그 테이블을 바꾸는 계정의 권한으로 실행된다. WHEN 조건은 어떤 함수·연산자든 부를 수 있고(query_to_xml 같은
# 함수에 넘긴 SQL 문자열은 의존 기록도 남지 않는다) 트리거 함수가 불리지 않아도 실행되므로 모두 거부한다. 이 앱의 마이그레이션은
# WHEN 조건이 있는 트리거를 만들지 않는다(그런 트리거가 필요한 마이그레이션을 더하면 이 검사를 고쳐야 한다).
# 셋째 값은 WHEN 조건이 있는지다 (메시지용).
_UNSAFE_TRIGGERS = """
SELECT t.identity, f.identity, g.tgqual IS NOT NULL, pg_catalog.count(*) OVER ()
FROM pg_catalog.pg_roles r CROSS JOIN pg_catalog.pg_database d CROSS JOIN pg_catalog.pg_trigger g
JOIN pg_catalog.pg_class c ON c.oid = g.tgrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_proc p ON p.oid = g.tgfoid
CROSS JOIN LATERAL pg_catalog.pg_identify_object(
    'pg_catalog.pg_trigger'::pg_catalog.regclass::pg_catalog.oid, g.oid, 0) t
CROSS JOIN LATERAL pg_catalog.pg_identify_object('pg_catalog.pg_proc'::pg_catalog.regclass::pg_catalog.oid, p.oid, 0) f
WHERE r.rolname = %s AND n.nspname = 'public' AND d.datname = pg_catalog.current_database() AND NOT g.tgisinternal
  AND (g.tgqual IS NOT NULL
       OR pg_catalog.pg_is_other_temp_schema(p.pronamespace) OR p.pronamespace = pg_catalog.pg_my_temp_schema()
       OR pg_catalog.pg_has_role(r.oid, p.proowner, 'MEMBER')
       OR (p.proowner < 16384::pg_catalog.oid AND p.proowner <> 10::pg_catalog.oid)
       OR (p.proowner <> 10::pg_catalog.oid
           AND p.proowner <> (SELECT u.oid FROM pg_catalog.pg_roles u WHERE u.rolname = current_user)
           AND (pg_catalog.has_schema_privilege(p.proowner, n.oid, 'CREATE') OR p.proowner = n.nspowner
                OR p.proowner = d.datdba)))
ORDER BY 1, 2 LIMIT 10"""
# 거부 메시지용: public 스키마 테이블의 트리거(시스템이 만든 내부 트리거 제외) 모두. TRIGGER 권한을 거둬도 그 권한으로 이미 만든
# 트리거는 남는다.
_PUBLIC_TRIGGERS = """
SELECT t.identity, pg_catalog.count(*) OVER ()
FROM pg_catalog.pg_trigger g
JOIN pg_catalog.pg_class c ON c.oid = g.tgrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN LATERAL pg_catalog.pg_identify_object(
    'pg_catalog.pg_trigger'::pg_catalog.regclass::pg_catalog.oid, g.oid, 0) t
WHERE n.nspname = 'public' AND NOT g.tgisinternal
ORDER BY 1 LIMIT 10"""
# 서비스 계정이나 그 역할이 속한 역할(PUBLIC에 준 것 포함)이 public 스키마의 일반·파티션 테이블(ledger면 원장
# public.schema_migrations만)에 주어진 권한이 있는지. 열 단위 권한도 본다(has_table_privilege는 열 단위 권한을 보지 않는다.
# 열 단위 REFERENCES로도 외래 키를, 열 단위 INSERT로도 원장 행을 만들 수 있다. 테이블 단위 REVOKE는 열 단위 권한도 함께 거둔다).
# - REFERENCES: 외래 키 검사는 참조되는 테이블의 소유자(마이그레이션 계정) 권한으로 실행되므로, 서비스 계정이 자기 스키마의
#   테이블에서 public 테이블을 참조하는 외래 키를 걸고 그 열에 자기가 만든 암시적 캐스트를 쓰면 행을 넣을 때마다(마이그레이션과
#   관계없이) 그 함수를 마이그레이션 계정의 권한으로 실행하게 할 수 있다.
# - 원장의 INSERT·UPDATE·DELETE·TRUNCATE: 아직 적용하지 않은 마이그레이션 파일의 행(체크섬은 저장소에 공개)을 넣어 두면 실행기가
#   그 파일을 적용한 것으로 보고 건너뛴다. 원장이 아직 없으면(빈 DB) 거짓이다.
# 두 번째 값은 거부 메시지용: 테이블·열 ACL에서 그 권한을 받은 그 역할들(NULL이면 PUBLIC).
_TABLE_PRIVILEGE = """
SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles m CROSS JOIN pg_catalog.pg_class c
               WHERE pg_catalog.pg_has_role(r.oid, m.oid, 'MEMBER') AND c.relnamespace = n.oid
                 AND c.relkind IN ('r', 'p') AND (c.relname = 'schema_migrations' OR NOT %(ledger)s)
                 AND (pg_catalog.has_table_privilege(m.oid, c.oid, %(table)s)
                      OR pg_catalog.has_any_column_privilege(m.oid, c.oid, %(column)s))),
       ARRAY(SELECT DISTINCT m.rolname
             FROM pg_catalog.pg_class c
             CROSS JOIN LATERAL (SELECT c.relacl UNION ALL SELECT t.attacl FROM pg_catalog.pg_attribute t
                                 WHERE t.attrelid = c.oid AND t.attacl IS NOT NULL) l (acl)
             CROSS JOIN LATERAL pg_catalog.aclexplode(l.acl) a
             LEFT JOIN pg_catalog.pg_roles m ON m.oid = a.grantee
             WHERE c.relnamespace = n.oid AND c.relkind IN ('r', 'p') AND (c.relname = 'schema_migrations' OR NOT %(ledger)s)
               AND a.privilege_type = ANY (%(types)s::pg_catalog.text[])
               AND (m.oid IS NULL OR pg_catalog.pg_has_role(r.oid, m.oid, 'MEMBER'))
             ORDER BY 1)
FROM pg_catalog.pg_roles r CROSS JOIN pg_catalog.pg_namespace n
WHERE r.rolname = %(role)s AND n.nspname = 'public'"""
# 거부 메시지용: public 스키마 밖의 테이블에서 public 테이블을 참조하는 외래 키. REFERENCES 권한을 거둬도 그 권한으로 이미 만든
# 외래 키는 남는다(이 앱의 스키마·마이그레이션은 public 안의 외래 키만 만든다).
_OUTSIDE_FOREIGN_KEYS = """
SELECT o.identity, pg_catalog.count(*) OVER ()
FROM pg_catalog.pg_constraint k
JOIN pg_catalog.pg_class p ON p.oid = k.confrelid
JOIN pg_catalog.pg_class c ON c.oid = k.conrelid
JOIN pg_catalog.pg_namespace n ON n.oid = p.relnamespace
CROSS JOIN LATERAL pg_catalog.pg_identify_object(
    'pg_catalog.pg_constraint'::pg_catalog.regclass::pg_catalog.oid, k.oid, 0) o
WHERE k.contype = 'f' AND n.nspname = 'public' AND c.relnamespace <> n.oid
ORDER BY 1 LIMIT 10"""
# 서비스 계정이나 그 역할이 속한 역할이 소유한 이 DB의 스키마(예전 DB CREATE 권한으로 만든 것 등. public 스키마·DB의 소유는
# _CAN_CREATE가 먼저 거부한다). 그 안에 타입·캐스트·함수·테이블을 둘 수 있고(REFERENCES와 함께 쓰면 외래 키 검사로 마이그레이션
# 계정의 권한으로 함수를 실행하게 할 수 있다), 마이그레이션 계정 이름의 스키마는 대화형 세션의 search_path("$user")에서 public보다
# 먼저 찾는다. 임시 스키마는 부트스트랩 슈퍼유저가 소유하므로 걸리지 않는다. 둘째 값은 소유자 이름(메시지용)이다.
_OWNED_SCHEMAS = """
SELECT s.nspname, o.rolname, pg_catalog.count(*) OVER ()
FROM pg_catalog.pg_roles r CROSS JOIN pg_catalog.pg_namespace s
JOIN pg_catalog.pg_roles o ON o.oid = s.nspowner
WHERE r.rolname = %s AND pg_catalog.pg_has_role(r.oid, s.nspowner, 'MEMBER')
ORDER BY 1 LIMIT 10"""
# 빈 DB의 기본 스키마가 만드는 확장 중 아직 없는 것이 있는지 (슈퍼유저가 아닌 계정이 확장을 만들려면 DB CREATE가 필요하다)
_MISSING_EXTENSIONS = """
SELECT pg_catalog.count(*) < 2 FROM pg_catalog.pg_extension e WHERE e.extname IN ('pgcrypto', 'vector')"""


class MigrationError(RuntimeError):
    pass


def _sql(path: Path) -> str:
    """서버로 보낼 SQL. pg_dump가 넣는 \\restrict 같은 psql 메타 명령 줄은 뺀다."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    return "\n".join(line for line in lines if not line.startswith("\\"))


def checksum(path: Path) -> str:
    """SQL 본문의 SHA-256. 주석만 있는 줄·빈 줄·줄 끝 공백·CRLF·BOM 차이는 무시한다 (설명 주석 수정은 허용)."""
    lines = (line.rstrip() for line in path.read_text(encoding="utf-8-sig").splitlines())
    body = "\n".join(line for line in lines if line and not line.lstrip().startswith("--"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def migration_files(db_dir: Path = DB_DIR) -> list[Path]:
    """DB/migrations/NN_name.sql(영문·숫자·_만)을 번호(정수) 순으로 돌려준다. 이름 형식이 틀리거나 번호가 겹치면 MigrationError."""
    files = list((db_dir / "migrations").glob("*.sql"))
    bad = sorted(f.name for f in files if not _NAME.match(f.name))
    if bad:
        raise MigrationError(f"마이그레이션 파일 이름은 NN_name.sql 형식(영문·숫자·_만, 예: 09_add_example_table.sql)이어야 합니다: {bad}")
    files.sort(key=lambda f: (int(_NAME.match(f.name).group(1)), f.name))
    numbers = [int(_NAME.match(f.name).group(1)) for f in files]
    if len(numbers) != len(set(numbers)):
        raise MigrationError(f"마이그레이션 번호가 겹칩니다: {[f.name for f in files]}")
    return files


def bootstrap_files(*, seed: bool, db_dir: Path = DB_DIR) -> list[Path]:
    """빈 DB에 마이그레이션보다 먼저 적용하는 파일 (기본 스키마 → 시드). docker-compose initdb도 같은 순서다."""
    return [db_dir / rel for rel in BASELINE + ((SEED,) if seed else ())]


def _create_refusal(conn: psycopg.Connection, role: str, *, schema: bool, db: bool) -> str:
    """public 스키마·DB에 객체를 만들 수 있는 서비스 계정의 거부 메시지: 걸린 검사, 그 권한·소유를 가진 역할, 고칠 문장.

    메시지의 SQL을 그대로 실행할 수 있게 이름은 따옴표로 감싸고, 나온 순서대로 실행하면 되게 쓴다. 소유를 먼저 바꾸게 쓴다:
    소유자는 거둔 CREATE를 스스로 다시 줄 수 있고, 소유자에게서 CREATE를 먼저 거두면 소유자를 바꿀 때 새 소유자도 그 CREATE를
    받지 못한다(이미 거둔 상태면 마이그레이션에 필요한 CREATE를 새 소유자에게 다시 주는 GRANT 문을 붙인다).
    소속 역할에서 소속을 거두는 문(REVOKE "<소속 역할>" FROM "<서비스 계정>")은 주지 않는다: 서비스 계정이 그 역할로 public에
    심어 둔 객체가 남은 채 소속만 없어지는데, 아래 문들은 소속을 그대로 두므로 다음 실행의 소유 객체 검사가 그 객체를 찾는다.
    """
    def q(name: str) -> str:
        return sql.Identifier(name).as_string(conn)

    def holder(name: str, is_self: bool) -> str:
        return "그 역할 자신" if is_self else f"그 역할이 속한 역할 {q(name)}"

    regranted = "(지금 소유자 몫의 CREATE를 거둔 상태라 새 소유자에게 다시 줌)"
    role_q, db_q = q(role), q(conn.info.dbname)
    migrator, superuser = conn.execute(
        "SELECT r.rolname, r.rolsuper FROM pg_catalog.pg_roles r WHERE r.rolname = current_user").fetchone()
    migrator_q = q(migrator)
    rows = conn.execute(_CREATE_HOLDERS, (role,)).fetchall()
    owners = {kind: (name, is_self, can) for kind, name, is_self, can in rows if kind.endswith("owner")}
    schema_owner = owners.get("schema owner", ("",))[0]
    schema_fix = "schema owner" in owners and schema_owner != "pg_database_owner"
    fixes = []
    # 슈퍼유저가 아닌 마이그레이션 계정(RDS 마스터 사용자)은 지금 소유자의 권한이 있어야 소유자를 바꿀 수 있다.
    current = list(dict.fromkeys(owners[kind][0] for kind in ("db owner", "schema owner")
                                 if kind in owners and owners[kind][0] != "pg_database_owner"))
    if current and not superuser:
        inherit = " WITH INHERIT TRUE" if conn.info.server_version >= 160000 else ""
        fixes.append("마이그레이션 계정이 슈퍼유저가 아니면(RDS 마스터 사용자 등) 먼저 지금 소유자의 권한을 받습니다: "
                     + " ".join(f"GRANT {q(name)} TO {migrator_q}{inherit};" for name in current))
    if "db owner" in owners:
        name, is_self, can = owners["db owner"]
        also = ", pg_database_owner로 public 스키마도 소유" if schema_owner == "pg_database_owner" else ""
        regrant = ""
        # DB CREATE는 확장(pgcrypto·vector)을 아직 만들지 않았거나 public 스키마의 소유자도 바꿀 때만 필요하다.
        if not can and (schema_fix or conn.execute(_MISSING_EXTENSIONS).fetchone()[0]):
            regrant += f" GRANT CREATE ON DATABASE {db_q} TO {migrator_q};"
        if also and not owners["schema owner"][2]:  # 새 DB 소유자는 pg_database_owner로 public 스키마를 받는다
            regrant += " GRANT CREATE ON SCHEMA public TO pg_database_owner;"
        fixes.append(f"DB 소유자({holder(name, is_self)}{also}): ALTER DATABASE {db_q} OWNER TO {migrator_q};"
                     + (regrant + regranted if regrant else ""))
    if schema_fix:
        name, is_self, can = owners["schema owner"]
        new_owner = "pg_database_owner" if conn.info.server_version >= 150000 else migrator_q
        fixes.append(f"public 스키마 소유자({holder(name, is_self)}): ALTER SCHEMA public OWNER TO {new_owner};"
                     + ("" if can else f" GRANT CREATE ON SCHEMA public TO {new_owner};{regranted}"))
    for kind, check, target, explained in (("schema", schema, "SCHEMA public", "schema owner"),
                                           ("db", db, f"DATABASE {db_q}", "db owner")):
        # pg_database_owner에 준 CREATE는 DB 소유자(위의 DB 소유)의 것이다.
        grants = [(name, is_self) for k, name, is_self, _ in rows if k == kind and name != "pg_database_owner"]
        if not check or (explained in owners and not grants):  # 소유만으로 걸렸으면 소유자를 바꾸면 된다
            continue
        members = "".join(f" CREATE를 받은 소속 역할 {q(name)}: REVOKE CREATE ON {target} FROM {q(name)};"
                          for name, is_self in grants if name is not None and not is_self)
        if kind == "schema":
            fixes.append("public 스키마: REVOKE CREATE ON SCHEMA public FROM PUBLIC;(PostgreSQL 14 이하에서 만들었거나 업그레이드·"
                         "복원한 DB의 기본값)을 실행하고 그 역할이나 그 역할이 속한 역할에 따로 준 CREATE도 거둡니다"
                         f"(예: REVOKE CREATE ON SCHEMA public FROM {role_q};).{members}")
        else:
            fixes.append(f"DB: REVOKE CREATE ON DATABASE {db_q} FROM PUBLIC;을 실행하고 그 역할이나 그 역할이 속한 역할에 준 CREATE도"
                         f" 거둡니다(예: REVOKE CREATE ON DATABASE {db_q} FROM {role_q};).{members}")
    failed = (["public 스키마"] if schema else []) + ([f"DB {db_q}"] if db else [])
    return (f"DB_APP_ROLE이 {'와 '.join(failed)}에 객체를 만들 수 있습니다(CREATE 권한이나 소유, 그 역할이 속한 역할의 것 포함):"
            f" {role_q}. 마이그레이션 계정으로 다음을 고친 뒤 다시 실행하세요. {' '.join(fixes)}")


def check_app_role(conn: psycopg.Connection, role: str) -> None:
    """BE 서비스 계정 검사. 없는 역할, 특권 계정, public 스키마·DB에 객체를 만들 수 있는 계정(CREATE 권한이나 소유),
    public 스키마에 객체를 소유한 계정(기본 제공 역할과 public에 객체를 만들 수 있는 다른 역할이 소유한 객체 포함), public 스키마의
    테이블에 TRIGGER 권한이 있는 계정이면 MigrationError (그 역할이 속한 역할의 것 포함). public 스키마 테이블의 트리거에 WHEN
    조건이 있거나 그 트리거가 임시 스키마의 함수나 그 역할들이 소유한 함수를 불러도 MigrationError. 그 역할이나 속한 역할(또는
    PUBLIC)이 public 스키마 테이블에 REFERENCES 권한이나 원장에 쓰기 권한이 있거나, 그 역할이나 속한 역할이 이 DB의 스키마를
    소유해도 MigrationError. 아무것도 바꾸지 않는다. lock 연결(search_path = pg_catalog)에서 부른다."""
    row = conn.execute(_PRIVILEGED_ROLE, (role,)).fetchone()
    if row is None:
        raise MigrationError(f"DB_APP_ROLE 역할이 없습니다: {role} (마이그레이션 계정으로 먼저 CREATE ROLE ... LOGIN)")
    role_q = sql.Identifier(role).as_string(conn)
    if row[0]:
        members = ", ".join(sql.Identifier(name).as_string(conn) for name in row[1])
        raise MigrationError(f"DB_APP_ROLE은 마이그레이션 계정과 다른, 특권 없는 역할이어야 합니다: {role_q}"
                             + (f" (SUPERUSER·CREATEROLE·CREATEDB·REPLICATION·BYPASSRLS 속성이 있는 소속 역할: {members})"
                                if members else ""))
    # 검사 결과가 없으면(NULL) 만들 수 있는 것으로 본다 (fail-closed).
    can_schema, can_db = conn.execute(_CAN_CREATE, (role,)).fetchone()
    if can_schema is not False or can_db is not False:
        raise MigrationError(_create_refusal(conn, role, schema=can_schema is not False, db=can_db is not False))
    owned = conn.execute(_OWNED_IN_PUBLIC, (role,)).fetchall()
    if owned:
        objects = [f"{kind} {identity}" for kind, identity, _ in owned]  # 이름에 줄바꿈이 있어도 한 줄로 (repr)
        rest = f" 외 {owned[0][2] - len(owned)}개" if owned[0][2] > len(owned) else ""
        migrator_q = sql.Identifier(conn.execute("SELECT current_user").fetchone()[0]).as_string(conn)
        raise MigrationError(
            "DB_APP_ROLE이나 그 역할이 속한 역할, 기본 제공 역할(pg_database_owner·pg_monitor 등), public 스키마에 CREATE 권한이"
            " 있거나 DB·public 스키마를 소유한 다른 역할이 public 스키마에 객체를"
            f" 소유합니다: {role_q} {objects}{rest}."
            " 마이그레이션 계정과 앱이 search_path로 찾아 부르는 계정의 권한으로 실행할 수 있으니 마이그레이션 계정으로 하나씩 확인해"
            " 예상하지 못한 객체는 DROP하고, 앱에 필요한 객체는 소유자를 마이그레이션 계정으로 바꾼 뒤"
            f"(ALTER <종류> <이름> OWNER TO {migrator_q}) 다시 실행하세요.")
    # 트리거: TRIGGER 권한이 있으면 마이그레이션이 바꾸는 테이블에 함수를 걸 수 있고, 이미 걸린 트리거도 본다.
    can_trigger, holders = conn.execute(_CAN_TRIGGER, (role,)).fetchone() or (True, [])
    triggers = conn.execute(_UNSAFE_TRIGGERS, (role,)).fetchall()
    problems = []
    if can_trigger is not False:
        revokes = " ".join("REVOKE TRIGGER ON ALL TABLES IN SCHEMA public FROM"
                           f" {'PUBLIC' if name is None else sql.Identifier(name).as_string(conn)};"
                           for name in holders or [role])
        # 그 권한으로 이미 만든 트리거(WHEN 조건만 심은 것 등)는 REVOKE 뒤에도 남으므로 지금 있는 트리거를 모두 알려 준다.
        existing = conn.execute(_PUBLIC_TRIGGERS).fetchall()
        found = "없음"
        if existing:
            found = f"{[f'trigger {trigger}' for trigger, _ in existing]}" + (
                f" 외 {existing[0][1] - len(existing)}개" if existing[0][1] > len(existing) else "")
        problems.append("DB_APP_ROLE이나 그 역할이 속한 역할이 public 스키마의 테이블에 TRIGGER 권한이 있습니다(트리거로 그 테이블을"
                        f" 바꾸는 마이그레이션 계정의 권한으로 함수를 실행하게 할 수 있음): {role_q}. 마이그레이션 계정으로 {revokes}를"
                        " 실행하세요. 그 권한이 있던 동안 만든 트리거는 권한을 거둔 뒤에도 남으니 public 스키마 테이블의 트리거(시스템 내부"
                        f" 트리거 제외: {found})를 마이그레이션 계정으로 확인해 예상하지 못한 트리거는 DROP TRIGGER <이름> ON <테이블>;로"
                        " 지우세요.")
    if triggers:
        listed = [f"trigger {trigger}{' (WHEN 조건)' if when else ''} → function {function}"
                  for trigger, function, when, _ in triggers]
        rest = f" 외 {triggers[0][3] - len(triggers)}개" if triggers[0][3] > len(triggers) else ""
        problems.append("public 스키마 테이블의 트리거에 WHEN 조건이 있거나 그 트리거가 임시 스키마의 함수나 DB_APP_ROLE·그 역할이"
                        f" 속한 역할·기본 제공 역할·public에 객체를 만들 수 있는 다른 역할이 소유한 함수를 부릅니다: {role_q} {listed}"
                        f"{rest}. 그 테이블을 바꾸는 마이그레이션 계정·앱의 권한으로 실행되니(이 앱의 마이그레이션은 WHEN 조건이 있는"
                        " 트리거를 만들지 않습니다) 마이그레이션 계정으로 확인해 예상하지 못한 트리거는 DROP TRIGGER <이름> ON <테이블>;로"
                        " 지우세요(임시 스키마의 함수를 부르는 트리거는 그 함수를 만든 세션이 끝나면 함께 지워집니다).")
    # REFERENCES·원장 쓰기 권한: 그 역할 자신뿐 아니라 속한 역할·PUBLIC에 준 것도 본다(실행이 끝날 때 거두는 것은 그 역할 자신의
    # 권한뿐이다). 메시지의 REVOKE는 ACL에서 그 권한을 받은 역할마다 하나씩 준다.
    def q(name: str) -> str:
        return sql.Identifier(name).as_string(conn)

    def revoke_each(statement: str, holders: list) -> str:
        return " ".join(f"{statement} {'PUBLIC' if name is None else q(name)};" for name in holders or [role])

    can_reference, holders = conn.execute(_TABLE_PRIVILEGE, {
        "role": role, "ledger": False, "table": "REFERENCES", "column": "REFERENCES",
        "types": ["REFERENCES"]}).fetchone() or (True, [])
    if can_reference is not False:
        # 그 권한이 있던 동안 만든 외래 키는 REVOKE 뒤에도 남으므로 public 밖에서 public 테이블을 참조하는 외래 키를 알려 준다.
        existing = conn.execute(_OUTSIDE_FOREIGN_KEYS).fetchall()
        found = "없음"
        if existing:
            found = f"{[f'constraint {key}' for key, _ in existing]}" + (
                f" 외 {existing[0][1] - len(existing)}개" if existing[0][1] > len(existing) else "")
        problems.append("DB_APP_ROLE이나 그 역할이 속한 역할(또는 PUBLIC)이 public 스키마의 테이블에 REFERENCES 권한이 있습니다(외래 키"
                        " 검사는 참조되는 테이블 소유자인 마이그레이션 계정의 권한으로 실행되므로 그 역할이 만든 캐스트 같은 함수를 언제든"
                        f" 마이그레이션 계정의 권한으로 실행하게 할 수 있음): {role_q}. 마이그레이션 계정으로"
                        f" {revoke_each('REVOKE REFERENCES ON ALL TABLES IN SCHEMA public FROM', holders)}를 실행하세요. 그 권한이"
                        " 있던 동안 만든 외래 키는 권한을 거둔 뒤에도 남으니 public 스키마 밖의 테이블에서 public 테이블을 참조하는"
                        f" 외래 키({found})를 마이그레이션 계정으로 확인해 예상하지 못한 것은"
                        " ALTER TABLE <테이블> DROP CONSTRAINT <이름>;으로 지우세요.")
    can_write, holders = conn.execute(_TABLE_PRIVILEGE, {
        "role": role, "ledger": True, "table": "INSERT, UPDATE, DELETE, TRUNCATE", "column": "INSERT, UPDATE",
        "types": ["INSERT", "UPDATE", "DELETE", "TRUNCATE"]}).fetchone() or (True, [])
    if can_write is not False:
        problems.append("DB_APP_ROLE이나 그 역할이 속한 역할(또는 PUBLIC)이 원장 public.schema_migrations에 INSERT·UPDATE·DELETE·"
                        "TRUNCATE 권한이 있습니다(아직 적용하지 않은 마이그레이션을 적용한 것으로 기록해 건너뛰게 할 수 있음):"
                        f" {role_q}. 마이그레이션 계정으로 {revoke_each('REVOKE ALL ON public.schema_migrations FROM', holders)}를"
                        " 실행하세요. 그 권한이 있던 동안 넣거나 바꾼 원장 행은 권한을 거둔 뒤에도 남으니 원장의 행이 실제로 적용한"
                        " 마이그레이션과 맞는지 마이그레이션 계정으로 확인하세요.")
    schemas = conn.execute(_OWNED_SCHEMAS, (role,)).fetchall()
    if schemas:
        migrator, superuser = conn.execute(
            "SELECT r.rolname, r.rolsuper FROM pg_catalog.pg_roles r WHERE r.rolname = current_user").fetchone()
        listed = [f"{q(name)} (소유자 {q(owner)})" for name, owner, _ in schemas]
        rest = f" 외 {schemas[0][2] - len(schemas)}개" if schemas[0][2] > len(schemas) else ""
        grant = ""
        if not superuser:  # 슈퍼유저가 아닌 마이그레이션 계정(RDS 마스터 사용자)은 지금 소유자의 권한이 있어야 지우거나 바꿀 수 있다
            inherit = " WITH INHERIT TRUE" if conn.info.server_version >= 160000 else ""
            grant = ("마이그레이션 계정이 슈퍼유저가 아니면(RDS 마스터 사용자 등) 먼저 지금 소유자의 권한을 받습니다: "
                     + " ".join(f"GRANT {q(owner)} TO {q(migrator)}{inherit};"
                                for owner in dict.fromkeys(owner for _, owner, _ in schemas)) + " ")
        fixes = " ".join(f"DROP SCHEMA {q(name)} CASCADE; 또는 ALTER SCHEMA {q(name)} OWNER TO {q(migrator)};"
                         for name, _, _ in schemas)
        problems.append("DB_APP_ROLE이나 그 역할이 속한 역할이 이 DB의 스키마를 소유합니다(예전 DB CREATE 권한으로 만든 것 등. 그 안의"
                        " 타입·캐스트·함수를 외래 키 검사 등으로 실행하게 할 수 있고, 마이그레이션 계정 이름의 스키마는 그 계정의 대화형"
                        ' 세션이 search_path의 "$user"로 public보다 먼저 찾음):'
                        f" {role_q} {listed}{rest}. 마이그레이션 계정으로 그 안의 객체를 확인한 뒤 {grant}스키마마다"
                        f" {fixes}를 실행하세요(ALTER SCHEMA는 그 안 객체의 소유자를 바꾸지 않으니 남긴 객체 중 예상하지 못한 것은"
                        " 지웁니다).")
    if problems:
        raise MigrationError(" ".join(problems) + " 그다음 다시 실행하세요.")


def grant_app_role(conn: psycopg.Connection, role: str) -> None:
    """BE 서비스 계정을 다시 검사(check_app_role)한 뒤 public 테이블·시퀀스에 있던 그 계정의 권한을 모두 거두고 지금 있는 앱
    테이블·시퀀스의 DML 권한만 준다 (한 트랜잭션, 여러 번 실행해도 같다)."""
    check_app_role(conn, role)
    names = {"role": sql.Identifier(role), "db": sql.Identifier(conn.info.dbname)}
    with conn.transaction():
        for statement in _APP_ROLE_GRANTS:
            conn.execute(sql.SQL(statement).format(**names))


def _connect(conn_kwargs: dict[str, Any]) -> psycopg.Connection:
    # 접속 인자가 없으면 앱과 같은 DB_* 환경변수(get_db_connection)로 접속한다.
    conn = psycopg.connect(**conn_kwargs) if conn_kwargs else get_db_connection()
    conn.autocommit = True
    conn.row_factory = tuple_row
    # DB·역할에 건 search_path 설정(ALTER DATABASE/ROLE ... SET)이나 마이그레이션 계정 이름의 스키마("$user")가 아니라 public만
    # 찾게 고정한다. pg_catalog는 적지 않아도 맨 먼저 찾으므로 다른 스키마에 심은 함수·연산자는 실행기에서 불리지 않는다.
    # public의 것은 인자 형이 pg_catalog의 것보다 정확히 맞으면 불리므로 실행기 자신의 SQL은 lock 연결(pg_catalog만)에서 실행한다.
    conn.execute("SET search_path TO public")
    return conn


def _connect_waiting(conn_kwargs: dict[str, Any], wait_seconds: float) -> psycopg.Connection:
    """DB가 막 뜨는 중이면(Compose initdb 직후 등) wait_seconds 동안 다시 시도한다."""
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            return _connect(conn_kwargs)
        except psycopg.OperationalError as exc:
            if time.monotonic() >= deadline:
                raise
            reason = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
            logger.info("DB 연결 대기 중: %s", reason)
            time.sleep(2)


def migrate(*, seed: bool = False, production: bool = False, db_dir: Path = DB_DIR, wait_seconds: float = 0,
            app_role: str | None = None, **conn_kwargs: Any) -> list[str]:
    """아직 적용하지 않은 마이그레이션을 번호 순으로 적용하고, 이번에 적용한 파일 이름을 돌려준다.

    conn_kwargs(host, port, user, password, dbname 등)를 주면 psycopg.connect에 그대로 넘기고, 없으면 앱과 같은
    DB_* 환경변수로 접속한다. 빈 DB에 넣은 기본 스키마·시드는 반환값에 넣지 않는다 (로그만 남긴다).
    production=True(CLI는 APP_ENV로 정한다)면 DB에 접속하기 전에 seed를 거부하고, 적용을 마친 뒤 SEED_PASSWORD_HASH를
    쓰는 계정이 있으면 MigrationError를 낸다 (이미 적용한 마이그레이션은 그대로 남는다).
    app_role을 주면 lock을 잡은 직후(기본 스키마·마이그레이션 SQL보다 먼저) check_app_role로 그 계정을 검사하고,
    마지막에 grant_app_role로 다시 검사한 뒤 그 계정의 권한을 다시 맞춘다.
    """
    if seed and production:
        raise MigrationError("--seed(시드 테스트 계정)는 로컬·테스트 전용이라 APP_ENV=production에서는 쓸 수 없습니다.")
    files = migration_files(db_dir)
    with _connect_waiting(conn_kwargs, wait_seconds) as lock_conn:
        # 실행기 자신의 SQL(서비스 계정 검사·원장 읽기·시드 해시 확인·GRANT)은 pg_catalog만 찾는다: public에 심은 함수·연산자
        # (예: 형이 정확히 맞는 public.=(oid, regclass)·=(varchar, varchar))도 부르지 않는다. 임시 스키마는 함수·연산자를 찾을 때
        # 쓰지 않는다. 기본 스키마·마이그레이션 파일은 public을 찾는 새 연결(_connect)에서 실행한다.
        lock_conn.execute("SET search_path TO pg_catalog")
        lock_conn.execute("SELECT pg_catalog.pg_advisory_lock(%s)", (_LOCK_KEY,))  # 연결이 닫히면 풀린다
        if app_role:
            # 서비스 계정이 심은 객체가 있거나 심을 수 있으면 마이그레이션 계정이 SQL을 하나라도 실행하기 전에 멈춘다.
            check_app_role(lock_conn, app_role)
        has_ledger, has_users = lock_conn.execute(
            "SELECT pg_catalog.to_regclass('public.schema_migrations') IS NOT NULL,"
            " pg_catalog.to_regclass('public.users') IS NOT NULL"
        ).fetchone()

        if not has_ledger:
            with _connect(conn_kwargs) as conn, conn.transaction():
                if has_users:
                    logger.info("원장이 없는 기존 DB입니다 (docker-compose initdb 등). 마이그레이션을 모두 다시 적용하고 기록합니다.")
                else:
                    # 빈 DB: 기본 스키마(+시드)와 원장을 한 트랜잭션으로. 실패하면 빈 DB로 남아 다시 실행할 수 있다.
                    if seed and not (db_dir / SEED).is_file():
                        raise MigrationError(f"시드 파일이 없습니다: {db_dir / SEED} (--seed는 로컬·테스트 전용)")
                    for path in bootstrap_files(seed=seed, db_dir=db_dir):
                        logger.info("기본 스키마 적용: %s", path.relative_to(db_dir).as_posix())
                        conn.execute(_sql(path))
                # 원장은 시드 뒤에 만든다. 시드가 search_path를 비우므로 public.을 붙여 쓴다.
                conn.execute(_LEDGER_DDL)
        if seed and (has_ledger or has_users):
            logger.info("이미 만들어진 DB라 시드는 넣지 않습니다 (--seed는 빈 DB를 처음 만들 때만 쓴다).")

        applied = dict(lock_conn.execute("SELECT filename, checksum FROM public.schema_migrations").fetchall())
        on_disk = {path.name: path for path in files}
        changed = sorted(name for name, recorded in applied.items()
                         if name in on_disk and recorded != checksum(on_disk[name]))
        if changed:
            raise MigrationError(f"적용한 뒤 SQL이 바뀐 마이그레이션: {changed}. 적용한 파일은 고치지 말고 다음 번호 파일로 추가하세요.")
        for name in sorted(set(applied) - set(on_disk)):
            logger.warning("DB에는 적용됐지만 이 코드에는 없는 마이그레이션: %s (이전 버전으로 되돌렸나요?)", name)

        done: list[str] = []
        for path in files:
            if path.name in applied:
                continue
            logger.info("마이그레이션 적용: %s", path.name)
            with _connect(conn_kwargs) as conn, conn.transaction():
                conn.execute(_sql(path))
                conn.execute("INSERT INTO public.schema_migrations (filename, checksum) VALUES (%s, %s)",
                             (path.name, checksum(path)))
            done.append(path.name)

        if production:
            seeded = lock_conn.execute("SELECT pg_catalog.count(*) FROM public.users WHERE password_hash = %s",
                                       (SEED_PASSWORD_HASH,)).fetchone()[0]
            if seeded:
                raise MigrationError(
                    f"운영 DB(APP_ENV=production)에 공개된 시드 테스트 비밀번호(04 마이그레이션의 해시)를 쓰는 계정이 {seeded}개"
                    " 있습니다. 시드·개발 DB 데이터가 들어간 것이니 그 계정을 지운 뒤 다시 실행하세요 (README 'AWS 배포 체크리스트').")
        if app_role:
            grant_app_role(lock_conn, app_role)
            logger.info("서비스 계정 권한 적용: %s (앱 테이블 DML만)", app_role)
        return done


def _production() -> bool:
    """CLI가 운영 검사를 할지. 앱(app.core.config)과 같이 APP_ENV를 읽고(비우면 development) development가 아니면 운영이다.

    production이 아닌 잘못된 값도 운영 검사를 하고, 접속할 때 app.core.config.db_password()가 그 값을 거부한다.
    """
    return (os.getenv("APP_ENV", "").strip().lower() or "development") != "development"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.migrate",
        description="DB_* 환경변수가 가리키는 DB에 기본 스키마와 DB/migrations를 적용한다 (원장: public.schema_migrations).",
    )
    parser.add_argument("--seed", action="store_true",
                        help="빈 DB를 처음 만들 때 시드(테스트 계정)도 넣는다. 로컬·테스트 전용이며 APP_ENV=production에서는 거부한다.")
    args = parser.parse_args(argv)
    load_dotenv()  # 로컬 실행용 BE/.env. 컨테이너·ECS에서는 이미 있는 환경변수가 우선한다.
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    try:
        # APP_ENV·DB_APP_ROLE은 .env를 읽은 뒤에 본다 (로컬 BE/.env는 development, 운영 이미지·ECS 태스크는 production).
        done = migrate(seed=args.seed, production=_production(), wait_seconds=60,
                       app_role=os.getenv("DB_APP_ROLE", "").strip() or None)
    # RuntimeError: MigrationError와 APP_ENV·DB_PASSWORD 설정 오류(app.core.config, 변수 이름만 담는다)
    except (RuntimeError, psycopg.Error, OSError) as exc:
        logger.error("마이그레이션 실패: %s", exc)
        return 1
    logger.info("완료: %s", ", ".join(done) if done else "새로 적용한 마이그레이션 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
