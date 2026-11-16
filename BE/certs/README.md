# RDS CA 인증서 묶음 (`rds-global-bundle.pem`)

BE가 Amazon RDS for PostgreSQL에 TLS로 접속할 때 서버 인증서를 확인하는 데 쓰는 **공개 CA 인증서 묶음**입니다(비밀값이
아닙니다). 운영 이미지(`BE/Dockerfile`)가 `COPY certs ./certs`로 `/app/certs/rds-global-bundle.pem`에 넣고
`PGSSLROOTCERT=/app/certs/rds-global-bundle.pem`을 기본값으로 둡니다. BE 서비스·마이그레이션 태스크(`python -m app.migrate`)·
시장 통계 반입 태스크가 모두 같은 이미지를 쓰므로 따로 파생 이미지를 만들지 않습니다.

- `APP_ENV=production`이면 `PGSSLMODE`가 `verify-full`(권장) 또는 `verify-ca`이고 `PGSSLROOTCERT`가 읽을 수 있는 파일이어야
  서버·마이그레이션 실행기가 시작합니다 (`app/core/config.py`의 `db_tls_options`). 작업 정의에는 `PGSSLMODE=verify-full`을
  넣고 `PGSSLROOTCERT`는 넣지 않거나 위 경로를 그대로 둡니다.
- RDS CA는 공개 신뢰 저장소(OS·certifi)에 없으므로 `PGSSLROOTCERT=system`으로 대신할 수 없습니다.
- 로컬 Compose(`APP_ENV=development`, TLS 없는 Postgres)는 `PGSSLMODE`를 넣지 않으면 libpq 기본값 `prefer`로 접속하므로 이
  파일이 있어도 동작이 같습니다.

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
  36개 리전과 `Beta`·`Preview`가 키 종류마다 하나씩 있습니다 (38 × 3). 이 배포 리전 **ap-northeast-2**의 세 인증서:
  - `Amazon RDS ap-northeast-2 Root CA RSA2048 G1` (만료 2061-05-20)
  - `Amazon RDS ap-northeast-2 Root CA RSA4096 G1` (만료 2121-05-20)
  - `Amazon RDS ap-northeast-2 Root CA ECC384 G1` (만료 2121-05-20)
- 유효 기간: 시작 2021-05-19 ~ 2026-09-02, 만료가 가장 이른 것 2061-05-19, 가장 늦은 것 2126-09-02. 받은 날짜 기준으로
  만료됐거나 아직 유효하지 않은 인증서는 없습니다.

## 갱신 방법

RDS가 새 CA를 내놓거나 DB 인스턴스의 CA를 바꾸기 전에 같은 주소에서 다시 받습니다 (다른 주소·미러에서 받지 않습니다).

```bash
cd BE/certs
curl --proto '=https' --tlsv1.2 -fsS -o rds-global-bundle.pem \
  https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
shasum -a 256 rds-global-bundle.pem                     # 이 문서의 SHA-256·받은 날짜를 새 값으로 고친다
grep -c 'BEGIN CERTIFICATE' rds-global-bundle.pem       # 인증서 수
openssl crl2pkcs7 -nocrl -certfile rds-global-bundle.pem \
  | openssl pkcs7 -print_certs -noout | grep 'ap-northeast-2'   # 이 리전의 CA가 들어 있는지
```

그다음 BE 이미지를 다시 빌드해 BE 서비스·마이그레이션·시장 통계 작업 정의를 모두 새 이미지 태그로 바꿉니다.
