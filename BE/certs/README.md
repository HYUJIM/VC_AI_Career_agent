# RDS CA 인증서 묶음 (`rds-global-bundle.pem`)

BE가 Amazon RDS for PostgreSQL에 TLS로 접속할 때 서버 인증서를 확인하는 데 쓰던 **공개 CA 인증서 묶음**입니다(비밀값이
아닙니다). 운영 이미지(`BE/Dockerfile`)가 `COPY certs ./certs`로 `/app/certs/rds-global-bundle.pem`에 넣고
`PGSSLROOTCERT=/app/certs/rds-global-bundle.pem`을 기본값으로 둡니다.

**지금 운영 구성(NCP 서버 한 대의 Docker Compose, [deploy/README.md](../../deploy/README.md))은 이 묶음을 쓰지 않습니다.**
DB가 같은 서버의 postgres 컨테이너이고, `deploy/scripts/init-secrets.sh`가 서버마다 만든 CA(`deploy/secrets/db-tls/ca.crt`)를
`deploy/docker-compose.yml`이 `PGSSLROOTCERT=/run/vc-db-tls/ca.crt`로 backend·migrate에 넣어 이미지 기본값을 덮어씁니다
([deploy/CHECKLIST.md](../../deploy/CHECKLIST.md) "DB TLS"). 파일은 이미지에 그대로 두었습니다(RDS로 다시 옮길 때와 이 값을
production 검사에 쓰는 테스트(`tests/conftest.py`의 `RDS_CA_BUNDLE`)를 위해).

- `APP_ENV=production`이면 `PGSSLMODE`가 `verify-full`(권장) 또는 `verify-ca`이고 `PGSSLROOTCERT`가 읽을 수 있는 파일이어야
  서버·마이그레이션 실행기가 시작합니다 (`app/core/config.py`의 `db_tls_options`). RDS에 붙인다면 `PGSSLMODE=verify-full`을 넣고
  `PGSSLROOTCERT`는 넣지 않거나 위 경로를 그대로 둡니다.
- RDS CA는 공개 신뢰 저장소(OS·certifi)에 없으므로 `PGSSLROOTCERT=system`으로 대신할 수 없습니다.
- 로컬 Compose(`APP_ENV=development`, TLS 없는 Postgres)는 `PGSSLMODE`를 넣지 않으면 libpq 기본값 `prefer`로 접속하므로 이
  파일이 있어도 동작이 같습니다.
- **RDS가 아닌 TLS PostgreSQL에 이 이미지를 붙일 때**(위 NCP 구성처럼 직접 운영하는 Postgres, 다른 클라우드 등): 이미지 기본값
  `PGSSLROOTCERT`(RDS 묶음)를 그 서버의 CA 파일로 바꿉니다(개발에서는 `PGSSLROOTCERT=`처럼 비워도 됨: 빈 값은 설정하지 않은
  것으로 봄). 그대로 두면 libpq는 CA 파일이 있을 때 `prefer`·`require`에서도 서버 인증서를 확인하므로, `prefer`(기본)는 확인에
  실패한 뒤 **조용히 TLS 없이** 다시 접속하고 `require`는 `certificate verify failed`로 실패합니다(TLS 서버·psql/libpq 16으로
  로컬 확인).

## 받은 파일

| 항목 | 값 |
|---|---|
| 출처 | https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem (모든 리전 묶음, AWS 문서 [Using SSL/TLS to encrypt a connection to a DB instance or cluster](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html)) |
| 받은 날짜 | 2026-11-16 (02:06 UTC) |
| 서버 응답 | HTTP 200, `Last-Modified: Wed, 14 Oct 2026 22:38:21 GMT`, `ETag: "e65e6ec2d36f4daa000c253841baec17"` |
| 크기 | 174,560 바이트 |
| SHA-256 | `90a6f1a7978a1b1b096dca6c57511ca48d3b8cf052f8e704baf358f34ea2f21b` |

받은 그대로 두었습니다(내용을 고치지 않음). openssl로 확인한 내용:

- PEM 인증서 블록만 있습니다 (`BEGIN CERTIFICATE` 114개, 다른 줄·CR 없음). 114개 모두 `openssl x509`로 읽히고
  `CA:TRUE`이며, 모두 자기 서명 루트 CA로 `openssl verify -CAfile rds-global-bundle.pem`을 통과합니다.
- 주체는 `CN=Amazon RDS <리전> Root CA <RSA2048|RSA4096|ECC384> G1, OU=Amazon RDS, O=Amazon Web Services, Inc.` 꼴이고,
  36개 리전과 `Beta`·`Preview`가 키 종류마다 하나씩 있습니다 (38 × 3). 예전 배포 리전(서울) **ap-northeast-2**의 세 인증서:
  - `Amazon RDS ap-northeast-2 Root CA RSA2048 G1` (만료 2061-05-20)
  - `Amazon RDS ap-northeast-2 Root CA RSA4096 G1` (만료 2121-05-20)
  - `Amazon RDS ap-northeast-2 Root CA ECC384 G1` (만료 2121-05-20)
- 유효 기간: 시작 2021-05-19 ~ 2026-09-02, 만료가 가장 이른 것 2061-05-19, 가장 늦은 것 2126-09-02. 받은 날짜 기준으로
  만료됐거나 아직 유효하지 않은 인증서는 없습니다.

## 갱신 방법

RDS에 붙여 쓰는 동안에는 RDS가 새 CA를 내놓거나 DB 인스턴스의 CA를 바꾸기 전에 같은 주소에서 다시 받습니다 (다른 주소·미러에서 받지 않습니다).

```bash
cd BE/certs
curl --proto '=https' --tlsv1.2 -fsS -o rds-global-bundle.pem \
  https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
shasum -a 256 rds-global-bundle.pem                     # 이 문서의 SHA-256·받은 날짜를 새 값으로 고친다
grep -c 'BEGIN CERTIFICATE' rds-global-bundle.pem       # 인증서 수
openssl crl2pkcs7 -nocrl -certfile rds-global-bundle.pem \
  | openssl pkcs7 -print_certs -noout | grep 'ap-northeast-2'   # 이 리전의 CA가 들어 있는지
```

그다음 BE 이미지를 다시 빌드합니다. 지금 운영 구성(NCP)은 이 묶음을 쓰지 않으므로 RDS에 붙일 때만 필요합니다.
