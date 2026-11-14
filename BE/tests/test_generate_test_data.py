"""개발용 더미 데이터 스크립트(generate_test_data.py).

공개된 비밀번호(TEST_PASSWORD)로 테스트 계정을 만드는 로컬·개발 전용 스크립트라, migrate --seed처럼 APP_ENV가
development(비우면 development)가 아니면 DB에 접속하기 전에 오류 한 줄(stderr)과 종료 코드 1로 끝난다.
Compose(backend)는 APP_ENV=development라 README의 `docker compose exec backend python generate_test_data.py`는 그대로다.
APP_ENV 검사는 접속 함수를 가짜로 바꿔 부르는지만 본다. 실제 실행은 임시 DB(TEST_DB_NAME + 임의 접미사, 끝나면 지움)에서
Compose 기본 DB(migrate --seed: user001~045가 이미 있음)·기본 스키마만 있는 빈 DB·중간 실패(전부 롤백, 종료 코드 1)를 본다.
CSV는 임시 폴더(cwd)에 쓰므로 저장소의 test_users_did_list.csv는 건드리지 않는다.
CSV는 커밋 전에 cwd의 임시 파일에 쓰고 커밋한 뒤 이름을 바꾼다: CSV 자리가 폴더면 DB 작업 전에 멈추고, 커밋 전 CSV 쓰기
실패는 전부 롤백되며, 커밋 뒤 이름 바꾸기 실패는 DB 변경이 남았다고 알리는지(어느 경우든 임시 파일은 남지 않음)도 본다.
기존 CSV를 바꿀 때는 예전 덮어쓰기처럼 권한 비트·(root면) 소유자를 남기고 심볼릭 링크는 따라가서 가리키는 파일을 바꾸는지,
SIGTERM·SIGHUP·Ctrl+C로 멈춰도(실제 신호를 받는 하위 프로세스) 임시 파일을 남기지 않고 커밋 뒤면 DB 변경이 남았다고 알리는지도 본다.
커밋 뒤(COMMIT 응답 직후·연결을 닫는 중 포함)에는 '커밋 전 중단' 안내를 내지 않는지, 무시하도록 물려받은 신호(nohup의 SIGHUP
등)는 계속 무시해 끝까지 실행하는지도 본다. 정리 중(커밋 뒤 실패 안내·finally)에 닿은 신호는 미뤄서, 다 저장한 실행의 finally면
더 알리지 않고 종료 코드 1, 이름 바꾸기 실패면 DB 변경이 남았다는 안내 한 줄만 남기고, 두 번째 신호에도 임시 파일을 남기지 않는지,
정리가 실패해도 원래 신호 처리기를 되돌리는지 본다.
"""
import builtins
import csv
import errno
import os
import signal
import stat
import subprocess
import sys
import time
import uuid

import pytest
from psycopg import sql

from tests.conftest import BE_DIR, TEST_DB_NAME, _conn_kwargs, _connect

CSV_NAME = "test_users_did_list.csv"
# 예전과 같은 CSV 모양: UTF-8 BOM, 같은 머리글(열 순서), CRLF 줄바꿈 (저장소의 test_users_did_list.csv와 같다)
CSV_HEADER = "\ufeff이름,이메일,발급된 배지 개수,조회용 DID (이 값을 복사해서 쓰세요)\r\n".encode()


@pytest.fixture
def script(monkeypatch):
    """generate_test_data 모듈과, 가짜 DB 접속이 불린 기록. 가짜 접속은 오류를 내서 그 자리에서 멈춘다."""
    import generate_test_data

    connects: list[int] = []

    def fake_connect(*args, **kwargs):
        connects.append(1)
        raise RuntimeError("테스트: DB 접속 대신 멈춤")

    monkeypatch.setattr(generate_test_data, "get_db_connection", fake_connect)
    monkeypatch.setattr(generate_test_data.psycopg, "connect", fake_connect)
    return generate_test_data, connects


def test_refuses_outside_development_before_connecting(script, monkeypatch, capsys):
    module, connects = script
    for value in ("production", " Production ", "prod", "staging"):
        monkeypatch.setenv("APP_ENV", value)
        with pytest.raises(SystemExit) as exc:
            module.main()
        assert exc.value.code == 1
        out, err = capsys.readouterr()
        assert out == "" and len(err.splitlines()) == 1 and "APP_ENV=development" in err
    assert connects == []  # 운영에서는 DB에 접속하지 않는다


def test_development_still_connects(script, monkeypatch, capsys, tmp_path):
    module, connects = script
    monkeypatch.chdir(tmp_path)
    for value in (None, "", "development", " Development "):
        if value is None:
            monkeypatch.delenv("APP_ENV", raising=False)
        else:
            monkeypatch.setenv("APP_ENV", value)
        with pytest.raises(SystemExit) as exc:
            module.main()  # 지금처럼 DB에 접속한다 (가짜 접속 오류는 '에러 발생' 한 줄(stderr)과 종료 코드 1)
        assert exc.value.code == 1
    assert len(connects) == 4
    out, err = capsys.readouterr()
    assert err.count("에러 발생") == 4 and "에러 발생" not in out
    assert list(tmp_path.iterdir()) == []  # 실패하면 CSV를 쓰지 않는다


# ── 임시 DB에서 실제로 실행 ──────────────────────────────────────────

@pytest.fixture
def scratch_db():
    """임시 DB를 만드는 함수. 끝나면 만든 DB를 지운다."""
    names: list[str] = []

    def _make() -> str:
        name = f"{TEST_DB_NAME}_gtd_{uuid.uuid4().hex[:8]}"
        with _connect("postgres") as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        names.append(name)
        return name

    yield _make
    with _connect("postgres") as admin:
        for name in names:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def _base_schema_db(scratch_db) -> str:
    """README의 시드 다시 만들기 경로처럼 DB/schema의 기본 스키마 두 파일만 적용한 빈 DB."""
    from app.migrate import _sql, bootstrap_files

    db = scratch_db()
    with _connect(db) as conn:
        for path in bootstrap_files(seed=False):
            conn.execute(_sql(path))
    return db


def _users(dbname: str) -> dict:
    """이메일 → (DID, 이름, 비밀번호 해시, 자격증명 수)."""
    with _connect(dbname) as conn:
        rows = conn.execute("SELECT u.email, u.subject_did, u.name, u.password_hash, count(c.record_id) FROM users u"
                            " LEFT JOIN credentials c ON c.user_id = u.id GROUP BY u.id").fetchall()
    return {email: (did, name, password_hash, count) for email, did, name, password_hash, count in rows}


def _run(module, dbname: str, monkeypatch, capsys, tmp_path):
    """임시 폴더(cwd)에서 그 DB로 main()을 실행하고 (종료 코드, stdout, stderr, CSV 데이터 행 또는 None)을 돌려준다."""
    monkeypatch.setenv("DB_NAME", dbname)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    try:
        module.main()
        code = 0
    except SystemExit as exc:
        code = exc.code
    out, err = capsys.readouterr()
    path = tmp_path / "test_users_did_list.csv"
    if not path.is_file():
        return code, out, err, None
    with path.open(encoding="utf-8-sig", newline="") as f:
        return code, out, err, list(csv.reader(f))[1:]


def _assert_only_the_csv_left(tmp_path) -> None:
    """cwd에는 CSV 하나만 남고(임시 파일 없음), CSV는 예전과 같은 모양(BOM·머리글·CRLF, 머리글 + 45줄)이며
    권한도 예전 open(..., 'w')로 새로 만든 파일과 같다(0o666 & ~umask: Compose의 root 실행에서도 호스트 사용자가 읽는다)."""
    assert [p.name for p in tmp_path.iterdir()] == [CSV_NAME]
    path = tmp_path / CSV_NAME
    data = path.read_bytes()
    assert data.startswith(CSV_HEADER) and data.endswith(b"\r\n") and data.count(b"\r\n") == 46
    umask = os.umask(0)
    os.umask(umask)
    assert stat.S_IMODE(path.stat().st_mode) == 0o666 & ~umask


def test_seeded_compose_database_reuses_the_existing_users(scratch_db, monkeypatch, capsys, tmp_path):
    """Compose 기본 DB(migrate --seed)에는 user001~045가 이미 있다: 그 계정(이름·DID·비밀번호)을 그대로 쓰고 배지만 더하며,
    CSV에는 그 계정의 실제 이름·DID를 쓴다 (예전에는 첫 배지 INSERT가 외래 키 오류로 멈추고 종료 코드 0이었다).
    지난 실행의 CSV는 새 CSV로 바뀐다."""
    import generate_test_data
    from app.migrate import migrate

    db = scratch_db()
    migrate(seed=True, **_conn_kwargs(db))
    before = _users(db)
    assert len(before) == 45
    (tmp_path / CSV_NAME).write_bytes(b"old csv")  # 지난 실행의 CSV

    code, out, err, rows = _run(generate_test_data, db, monkeypatch, capsys, tmp_path)
    assert (code, err) == (0, "")
    assert "새로 만듦 0명, 이미 있던 계정 45명" in out
    after = _users(db)
    assert set(after) == set(before)  # 새 계정은 없다
    assert rows is not None and len(rows) == 45
    for name, email, count, did in rows:
        assert (did, name) == before[email][:2]  # CSV는 DB의 실제 DID·이름
        assert after[email][:3] == before[email][:3]  # 이름·DID·비밀번호는 그대로
        assert after[email][3] - before[email][3] == int(count.removesuffix("개")) >= 5  # 배지는 그 계정에 들어간다
    _assert_only_the_csv_left(tmp_path)


def test_empty_base_schema_database_gets_45_new_users(scratch_db, monkeypatch, capsys, tmp_path):
    import generate_test_data

    db = _base_schema_db(scratch_db)
    code, out, err, rows = _run(generate_test_data, db, monkeypatch, capsys, tmp_path)
    assert (code, err) == (0, "")
    assert "새로 만듦 45명, 이미 있던 계정 0명" in out
    users = _users(db)
    assert len(users) == 45 and rows is not None and len(rows) == 45
    for name, email, count, did in rows:
        assert users[email][:2] == (did, name)
        assert users[email][3] == int(count.removesuffix("개"))
    assert len({password_hash for _, _, password_hash, _ in users.values()}) == 1  # 모두 TEST_PASSWORD 해시 하나
    _assert_only_the_csv_left(tmp_path)


def test_failure_midway_commits_nothing_and_exits_1(scratch_db, monkeypatch, capsys, tmp_path):
    """중간에 DB 오류가 나면(여기서는 100번째 배지 INSERT를 트리거가 거부) 앞서 넣은 유저·배지까지 모두 롤백되고,
    오류는 stderr의 '에러 발생' 줄과 종료 코드 1로 알리며 CSV는 쓰지 않는다."""
    import generate_test_data

    db = _base_schema_db(scratch_db)
    with _connect(db) as conn:
        conn.execute("CREATE SEQUENCE gtd_probe_seq")
        conn.execute("CREATE FUNCTION gtd_probe_fail() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN"
                     " IF nextval('gtd_probe_seq') >= 100 THEN RAISE EXCEPTION 'gtd probe: injected failure'; END IF;"
                     " RETURN NEW; END $$")
        conn.execute("CREATE TRIGGER gtd_probe BEFORE INSERT ON credentials FOR EACH ROW EXECUTE FUNCTION gtd_probe_fail()")

    code, out, err, rows = _run(generate_test_data, db, monkeypatch, capsys, tmp_path)
    assert code == 1
    assert err.startswith("에러 발생: ") and "injected failure" in err
    assert "CONTEXT:" in err  # 오류 내용 전체를 남긴다 (DB 오류의 CONTEXT 줄까지, 첫 줄만 자르지 않음)
    assert "완료" not in out and rows is None
    assert list(tmp_path.iterdir()) == []  # CSV도 임시 파일도 없다
    assert _users(db) == {}  # 먼저 넣은 유저 45명도 남지 않는다
    with _connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM credentials").fetchone()[0] == 0
        assert conn.execute("SELECT last_value FROM gtd_probe_seq").fetchone()[0] == 100  # 99개는 실제로 넣은 뒤 실패


# ── CSV를 저장하지 못할 때 ───────────────────────────────────────────

def test_csv_path_that_is_a_directory_stops_before_any_db_work(scratch_db, monkeypatch, capsys, tmp_path):
    """cwd의 test_users_did_list.csv가 폴더면 CSV를 저장할 수 없으므로 DB에 접속하기 전에 종료 코드 1로 멈춘다
    (예전에는 유저·배지를 커밋한 뒤 CSV를 열다가 실패해 DB 변경만 남았다). 운영 APP_ENV 거부가 여전히 먼저다."""
    import generate_test_data

    db = _base_schema_db(scratch_db)
    folder = tmp_path / CSV_NAME
    folder.mkdir()
    (folder / "keep.txt").write_text("그대로", encoding="utf-8")
    connects: list[int] = []
    real_connect = generate_test_data.get_db_connection

    def counting_connect():
        connects.append(1)
        return real_connect()

    monkeypatch.setattr(generate_test_data, "get_db_connection", counting_connect)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        generate_test_data.main()
    out, err = capsys.readouterr()
    assert exc.value.code == 1 and out == "" and "APP_ENV=development" in err  # 운영 거부가 먼저

    code, out, err, rows = _run(generate_test_data, db, monkeypatch, capsys, tmp_path)
    assert code == 1
    assert connects == []  # DB에 접속하지 않았다
    assert _users(db) == {}
    assert rows is None and "완료" not in out
    assert err.startswith("에러 발생: ") and len(err.splitlines()) == 1 and CSV_NAME in err and "폴더" in err
    assert [p.name for p in tmp_path.iterdir()] == [CSV_NAME]  # 임시 파일 없음
    assert [p.name for p in folder.iterdir()] == ["keep.txt"]  # 그 폴더는 건드리지 않는다


class _FailsAfterHeader:
    """머리글 줄은 쓰고 다음 줄에서 error를 내는 파일 (예: 디스크가 가득 참). with 문이 끝나면 닫는다."""

    def __init__(self, f, error: BaseException):
        self._f, self._error, self._writes = f, error, 0

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self._f.close()

    def write(self, text):
        self._writes += 1
        if self._writes > 1:
            raise self._error
        return self._f.write(text)


class _Interrupted(BaseException):
    """Ctrl+C(KeyboardInterrupt)처럼 except Exception에 잡히지 않는 중단. pytest가 KeyboardInterrupt를 따로 다루므로 대신 쓴다."""


@pytest.mark.parametrize("failure", ["open", "write"])
def test_csv_write_failure_before_commit_commits_nothing(scratch_db, monkeypatch, capsys, tmp_path, failure):
    """CSV는 커밋 전에(트랜잭션 안에서) cwd의 임시 파일에 쓴다: 임시 파일을 만들지 못하거나(open: 쓰기 권한 없음)
    쓰다가 실패하면(write: 디스크 부족) 유저·배지까지 모두 롤백되고 종료 코드 1이며, 쓰다 만 임시 파일도 남지 않는다
    (예전에는 커밋한 뒤에 CSV를 써서 DB 변경이 남았다)."""
    import generate_test_data

    db = _base_schema_db(scratch_db)
    opened: list[str] = []

    def failing_open(file, mode="r", *args, **kwargs):
        if not os.fspath(file).startswith(CSV_NAME):
            return builtins.open(file, mode, *args, **kwargs)
        opened.append(os.path.abspath(file))
        if failure == "open":
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), file)
        disk_full = OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        return _FailsAfterHeader(builtins.open(file, mode, *args, **kwargs), disk_full)

    monkeypatch.setattr(generate_test_data, "open", failing_open, raising=False)
    code, out, err, rows = _run(generate_test_data, db, monkeypatch, capsys, tmp_path)
    assert code == 1
    assert _users(db) == {}  # 유저 45명도 남지 않는다
    with _connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM credentials").fetchone()[0] == 0
    assert rows is None and "완료" not in out
    assert err.startswith("에러 발생: ") and os.strerror(errno.EACCES if failure == "open" else errno.ENOSPC) in err
    assert list(tmp_path.iterdir()) == []  # 임시 파일도 남지 않는다
    assert len(opened) == 1 and os.path.realpath(os.path.dirname(opened[0])) == os.path.realpath(tmp_path)  # cwd
    assert os.path.basename(opened[0]) != CSV_NAME  # CSV 자체가 아니라 임시 파일에 쓴다


def test_interrupt_while_writing_the_csv_rolls_back_and_leaves_no_temp_file(scratch_db, monkeypatch, tmp_path):
    """CSV 임시 파일을 쓰는 중에 중단되어도(Ctrl+C처럼 except Exception에 잡히지 않는 예외) DB는 롤백되고
    쓰다 만 임시 파일은 지운다. 중단 예외는 그대로 올라간다."""
    import generate_test_data

    db = _base_schema_db(scratch_db)

    def interrupted_open(file, mode="r", *args, **kwargs):
        f = builtins.open(file, mode, *args, **kwargs)
        return _FailsAfterHeader(f, _Interrupted()) if os.fspath(file).startswith(CSV_NAME) else f

    monkeypatch.setattr(generate_test_data, "open", interrupted_open, raising=False)
    monkeypatch.setenv("DB_NAME", db)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(_Interrupted):
        generate_test_data.main()
    assert _users(db) == {}  # 유저 45명도 남지 않는다
    assert list(tmp_path.iterdir()) == []  # 쓰다 만 임시 파일도 없다


@pytest.mark.parametrize("cause", ["locked", "folder"])
def test_csv_rename_failure_after_commit_keeps_the_db_changes_and_says_so(scratch_db, monkeypatch, capsys, tmp_path,
                                                                          cause):
    """커밋한 뒤 임시 파일을 CSV 이름으로 바꾸지 못하면(locked: Windows에서 엑셀로 열어 둔 CSV처럼 os.replace가
    PermissionError, folder: 시작 검사 뒤에 같은 이름의 폴더가 생김) 종료 코드 1과 함께 DB 변경은 커밋되어 남았고 CSV는
    저장하지 못했다고 이유와 함께 stderr에 알린다. 임시 파일은 지우고, 지난 실행의 CSV는 그대로 둔다."""
    import generate_test_data

    db = _base_schema_db(scratch_db)
    target = tmp_path / CSV_NAME
    replaced: list[tuple[str, bool]] = []
    if cause == "locked":
        target.write_bytes(b"old csv")  # 지난 실행의 CSV (엑셀로 열려 있음)
        real_replace = os.replace

        def locked_replace(src, dst, *args, **kwargs):
            if os.fspath(dst) != CSV_NAME:
                return real_replace(src, dst, *args, **kwargs)
            replaced.append((os.path.abspath(src), os.path.isfile(src)))
            raise PermissionError(errno.EACCES, "엑셀이 연 파일이라 바꿀 수 없음(테스트)", os.fspath(src), None,
                                  os.fspath(dst))

        monkeypatch.setattr(generate_test_data.os, "replace", locked_replace)
    else:
        real_connect = generate_test_data.get_db_connection

        def connect_then_make_folder():
            target.mkdir()  # 시작 검사(폴더 아님)를 지난 뒤에 생긴 같은 이름의 폴더
            return real_connect()

        monkeypatch.setattr(generate_test_data, "get_db_connection", connect_then_make_folder)

    code, out, err, rows = _run(generate_test_data, db, monkeypatch, capsys, tmp_path)
    users = _users(db)
    badges = sum(count for _, _, _, count in users.values())
    assert code == 1 and "완료" not in out
    assert len(users) == 45 and badges >= 45 * 5  # DB 변경은 커밋되어 남는다
    assert err.startswith("에러 발생: ")
    assert f"DB 변경(새로 만든 유저 45명, 배지 {badges}개)은 이미 커밋되어 그대로 남지만" in err
    assert f"CSV 파일({CSV_NAME})은 저장하지 못했습니다" in err
    assert [p.name for p in tmp_path.iterdir()] == [CSV_NAME]  # 임시 파일은 남지 않는다
    if cause == "locked":
        assert "엑셀이 연 파일이라 바꿀 수 없음(테스트)" in err  # 이유도 함께
        assert len(replaced) == 1 and replaced[0][1]  # 커밋 뒤 cwd에 다 쓴 임시 파일이 있었고
        assert os.path.realpath(os.path.dirname(replaced[0][0])) == os.path.realpath(tmp_path)
        assert not os.path.exists(replaced[0][0])  # 그 임시 파일은 지웠다
        assert target.read_bytes() == b"old csv"  # 지난 실행의 CSV는 그대로
    else:
        assert os.strerror(errno.EISDIR) in err  # 이유도 함께
        assert target.is_dir() and list(target.iterdir()) == []


# ── 기존 CSV를 바꿀 때: 예전 덮어쓰기처럼 권한·소유자·심볼릭 링크를 남긴다 ─────────

_POSIX_ONLY = pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX 권한·소유자·신호")


@_POSIX_ONLY
@pytest.mark.parametrize("mode", [0o664, 0o600], ids=["0664", "0600"])
def test_existing_csv_keeps_its_permission_bits(scratch_db, monkeypatch, capsys, tmp_path, mode):
    """지난 실행의 CSV를 새 CSV로 바꿔도 그 권한 비트는 그대로다(예전 open(..., 'w') 덮어쓰기처럼. 바꾸기만 하면
    umask 022에서 0644). root가 아니면 소유자는 건드리지 않고, 끝나면 SIGTERM·SIGHUP 처리기도 원래대로 돌아온다."""
    import generate_test_data

    db = _base_schema_db(scratch_db)
    target = tmp_path / CSV_NAME
    target.write_bytes(b"old csv")
    target.chmod(mode)
    chowned: list[tuple] = []
    monkeypatch.setattr(generate_test_data.os, "geteuid", lambda: 1000)  # root가 아닌 사용자
    monkeypatch.setattr(generate_test_data.os, "chown", lambda *args: chowned.append(args))
    saved = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGHUP)}
    sigint_handler = signal.getsignal(signal.SIGINT)

    def callers_handler(signum, frame):  # main()을 부른 쪽이 둔 처리기
        pass

    for signum in saved:
        signal.signal(signum, callers_handler)
    try:
        code, out, err, rows = _run(generate_test_data, db, monkeypatch, capsys, tmp_path)
        assert all(signal.getsignal(signum) is callers_handler for signum in saved)  # 끝나면 원래 처리기로
        assert signal.getsignal(signal.SIGINT) is sigint_handler  # 커밋하는 동안 미뤘던 Ctrl+C 처리기도 원래대로
    finally:
        for signum, handler in saved.items():
            signal.signal(signum, handler)
    assert (code, err) == (0, "") and rows is not None and len(rows) == 45
    assert stat.S_IMODE(target.stat().st_mode) == mode
    assert chowned == []
    assert [p.name for p in tmp_path.iterdir()] == [CSV_NAME]  # 임시 파일 없음


@_POSIX_ONLY
def test_original_signal_handlers_come_back_even_if_the_cleanup_fails(scratch_db, monkeypatch, capsys, tmp_path):
    """finally의 정리(임시 파일 삭제)마저 실패해도(이름 바꾸기 실패 뒤 os.remove도 실패) 그 오류는 그대로 올라가고, SIGTERM·
    SIGHUP·Ctrl+C 처리기는 원래대로 돌아온다 (예전에는 정리가 실패하면 되돌리지 않아 Ctrl+C가 계속 미뤄졌다)."""
    import generate_test_data

    db = _base_schema_db(scratch_db)
    real_replace = os.replace

    def locked_replace(src, dst, *args, **kwargs):
        if os.fspath(dst) != CSV_NAME:
            return real_replace(src, dst, *args, **kwargs)
        raise PermissionError(errno.EACCES, "엑셀이 연 파일이라 바꿀 수 없음(테스트)")

    def failing_remove(path, *args, **kwargs):
        raise PermissionError(errno.EACCES, "지울 수 없음(테스트)")

    monkeypatch.setattr(generate_test_data.os, "replace", locked_replace)
    monkeypatch.setattr(generate_test_data.os, "remove", failing_remove)
    monkeypatch.setenv("DB_NAME", db)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    saved = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}

    def callers_handler(signum, frame):  # main()을 부른 쪽이 둔 처리기
        pass

    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, callers_handler)
    try:
        with pytest.raises(PermissionError, match="지울 수 없음"):
            generate_test_data.main()
        restored = {signum: signal.getsignal(signum) for signum in saved}
    finally:
        for signum, handler in saved.items():
            signal.signal(signum, handler)
    assert restored == {signal.SIGINT: saved[signal.SIGINT], signal.SIGTERM: callers_handler,
                        signal.SIGHUP: callers_handler}
    out, err = capsys.readouterr()
    assert "이미 커밋되어 그대로 남지만" in err and "완료" not in out  # 그 전의 안내는 그대로


@_POSIX_ONLY
def test_root_run_keeps_the_owner_of_the_existing_csv(scratch_db, monkeypatch, capsys, tmp_path):
    """root로 실행하면(Compose의 backend는 user: root, BE/는 바인드 마운트) 기존 CSV의 소유자·그룹을 임시 파일에 옮긴 뒤
    권한 비트를 옮기고(chown이 setgid 등을 지우므로 이 순서) 이름을 바꾼다: Linux 호스트의 CSV가 root 소유로 바뀌지 않는다.
    여기서는 root가 아니므로 geteuid·chown을 바꾸고, 기존 CSV의 소유자는 stat에서 다른 값(4242:4343)으로 보이게 한다."""
    import generate_test_data

    db = _base_schema_db(scratch_db)
    target = tmp_path / CSV_NAME
    target.write_bytes(b"old csv")
    target.chmod(0o640)
    real_stat, real_chmod = os.stat, os.chmod
    calls: list[tuple] = []

    def other_owner_stat(path, *args, **kwargs):
        st = real_stat(path, *args, **kwargs)
        if isinstance(path, str) and path == CSV_NAME:  # 스크립트가 보는 기존 CSV
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, 4242, 4343, st.st_size,
                                   int(st.st_atime), int(st.st_mtime), int(st.st_ctime)))
        return st

    def recording_chown(path, uid, gid):
        calls.append(("chown", os.fspath(path), uid, gid))

    def recording_chmod(path, mode, *args, **kwargs):
        if os.fspath(path).startswith(CSV_NAME):
            calls.append(("chmod", os.fspath(path), mode))
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(generate_test_data.os, "geteuid", lambda: 0)
    monkeypatch.setattr(generate_test_data.os, "stat", other_owner_stat)
    monkeypatch.setattr(generate_test_data.os, "chown", recording_chown)
    monkeypatch.setattr(generate_test_data.os, "chmod", recording_chmod)

    code, out, err, rows = _run(generate_test_data, db, monkeypatch, capsys, tmp_path)
    assert (code, err) == (0, "") and rows is not None and len(rows) == 45
    assert len(calls) == 2
    tmp_name = calls[0][1]
    assert tmp_name.startswith(CSV_NAME + ".") and tmp_name.endswith(".tmp")  # 기존 CSV가 아니라 cwd의 임시 파일에
    assert calls == [("chown", tmp_name, 4242, 4343), ("chmod", tmp_name, 0o640)]
    assert stat.S_IMODE(real_stat(target).st_mode) == 0o640
    assert [p.name for p in tmp_path.iterdir()] == [CSV_NAME]  # 임시 파일 없음


@_POSIX_ONLY
@pytest.mark.parametrize("existing", [True, False])
def test_csv_symlink_is_kept_and_its_target_is_replaced(scratch_db, monkeypatch, capsys, tmp_path, existing):
    """cwd의 test_users_did_list.csv가 심볼릭 링크면 예전 덮어쓰기처럼 링크는 그대로 두고 가리키는 파일에 새 CSV를 쓴다
    (그 파일의 권한도 그대로). 임시 파일은 가리키는 파일과 같은 폴더에 만들었다가 남기지 않는다. 가리키는 파일이 아직
    없으면(existing=False) 그 자리에 새로 만든다(권한은 새 파일과 같은 0o666 & ~umask)."""
    import generate_test_data

    db = _base_schema_db(scratch_db)
    cwd, data = tmp_path / "cwd", tmp_path / "data"
    cwd.mkdir()
    data.mkdir()
    real = data / "users.csv"
    if existing:
        real.write_bytes(b"old csv")
        real.chmod(0o664)
    link_text = os.path.join("..", "data", "users.csv")
    (cwd / CSV_NAME).symlink_to(link_text)

    code, out, err, rows = _run(generate_test_data, db, monkeypatch, capsys, cwd)
    assert (code, err) == (0, "") and rows is not None and len(rows) == 45  # 링크를 따라 읽은 새 CSV
    link = cwd / CSV_NAME
    assert link.is_symlink() and os.readlink(link) == link_text  # 링크는 그대로
    assert [p.name for p in cwd.iterdir()] == [CSV_NAME] and [p.name for p in data.iterdir()] == ["users.csv"]
    data_bytes = real.read_bytes()
    assert data_bytes.startswith(CSV_HEADER) and data_bytes.count(b"\r\n") == 46
    umask = os.umask(0)
    os.umask(umask)
    assert stat.S_IMODE(real.stat().st_mode) == (0o664 if existing else 0o666 & ~umask)


# ── SIGTERM·SIGHUP·Ctrl+C로 멈출 때 (실제 신호를 받는 하위 프로세스) ─────────────

# 하위 프로세스: main()을 실행하다 정해진 곳(커밋 뒤 이름 바꾸기 직전, 또는 커밋 전 CSV 임시 파일을 쓰는 중)에서 표시 파일을
# 만들고 기다린다(표시 파일을 지우면 계속한다). 테스트가 그때 신호를 보낸다. in_commit·in_close는 커밋이 끝난 바로 그
# 자리(COMMIT 응답 직후의 Transaction.__exit__ 안, 연결을 닫는 중)에서 하위 프로세스가 자기에게 신호를 보낸다.
# in_finally·in_rename_failure·twice_before_commit도 하위 프로세스가 스스로 보내고, 보낸 횟수만큼 표시 파일에 x를 더한다:
# in_finally는 다 저장한 뒤 finally의 임시 파일 확인 중에, in_rename_failure는 이름 바꾸기가 실패해 DB 변경이 남았다고 알리기
# 직전과 그 뒤 finally에서, twice_before_commit은 커밋 전 CSV 임시 파일을 쓰는 중과 그 중단의 finally에서 보낸다.
_HELD_RUN = r'''
import builtins, csv, os, signal, sys, time
signal.signal(signal.SIGINT, signal.default_int_handler)  # 물려받은 SIG_IGN이 있어도 Ctrl+C는 KeyboardInterrupt
import psycopg
import generate_test_data
where, marker, signame = sys.argv[1], sys.argv[2], sys.argv[3]

def hold():
    open(marker, "w").close()
    deadline = time.monotonic() + 60
    while os.path.exists(marker) and time.monotonic() < deadline:
        time.sleep(0.05)

if where == "after_commit":
    real_replace = os.replace
    def held_replace(src, dst):
        hold()
        return real_replace(src, dst)
    generate_test_data.os.replace = held_replace
elif where == "before_commit":
    real_writer = csv.writer
    class HeldWriter:
        def __init__(self, f):
            self.w, self.n = real_writer(f), 0
        def writerow(self, row):
            self.n += 1
            if self.n == 2:
                hold()
            return self.w.writerow(row)
    generate_test_data.csv.writer = HeldWriter
elif where == "in_commit":
    real_exit = psycopg.Transaction.__exit__
    def exit_then_signal(self, *exc_info):
        result = real_exit(self, *exc_info)
        if exc_info[0] is None:
            os.kill(os.getpid(), getattr(signal, signame))  # COMMIT은 끝났다
        return result
    psycopg.Transaction.__exit__ = exit_then_signal
elif where in ("in_finally", "in_rename_failure", "twice_before_commit"):
    state = {"armed": False, "fired": False}
    def fire():
        with open(marker, "a") as f:
            f.write("x")
        os.kill(os.getpid(), getattr(signal, signame))
    real_exists = os.path.exists
    def exists_with_signal(path):  # finally의 임시 파일 확인 중에 한 번
        result = real_exists(path)
        if state["armed"] and not state["fired"] and str(path).endswith(".tmp"):
            state["fired"] = True
            fire()
        return result
    generate_test_data.os.path.exists = exists_with_signal
    if where == "in_finally":
        real_replace = os.replace
        def replace_then_arm(src, dst):
            real_replace(src, dst)
            state["armed"] = True
        generate_test_data.os.replace = replace_then_arm
    elif where == "in_rename_failure":
        def locked_replace(src, dst):
            raise PermissionError(13, "엑셀이 연 파일이라 바꿀 수 없음(테스트)")
        generate_test_data.os.replace = locked_replace
        def print_with_signal(*args, **kwargs):
            if args and str(args[0]).startswith("에러 발생: DB 변경"):
                state["armed"] = True
                fire()  # DB 변경이 남았다는 안내 직전
            return builtins.print(*args, **kwargs)
        generate_test_data.print = print_with_signal
    else:
        real_writer = csv.writer
        class SignalWriter:
            def __init__(self, f):
                self.w, self.n = real_writer(f), 0
            def writerow(self, row):
                self.n += 1
                if self.n == 2:
                    state["armed"] = True
                    fire()  # 커밋 전 CSV 임시 파일을 쓰는 중
                return self.w.writerow(row)
        generate_test_data.csv.writer = SignalWriter
else:
    real_close = psycopg.Connection.close
    def close_with_signal(self):
        os.kill(os.getpid(), getattr(signal, signame))  # 커밋한 뒤 연결을 닫는 중
        return real_close(self)
    psycopg.Connection.close = close_with_signal
generate_test_data.main()
'''


def _run_and_signal(dbname: str, cwd, marker, where: str, signame: str, ignored: str | None = None):
    """하위 프로세스로 main()을 실행하고, 기다리는 곳에 닿으면 신호를 보낸다(after_commit·before_commit 말고는 하위 프로세스가
    스스로 보낸다). ignored로 준 신호는 nohup처럼 무시하도록 물려준 채 실행하고, 신호를 보낸 뒤 기다림을 풀어 준다.
    (종료 코드, stdout, stderr)."""
    env = dict(os.environ, DB_NAME=dbname, APP_ENV="development", PYTHONPATH=str(BE_DIR),
               PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    preexec = (lambda: signal.signal(getattr(signal, ignored), signal.SIG_IGN)) if ignored else None
    proc = subprocess.Popen([sys.executable, "-B", "-c", _HELD_RUN, where, str(marker), signame], cwd=cwd, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", preexec_fn=preexec)
    try:
        if where in ("after_commit", "before_commit"):
            deadline = time.monotonic() + 120
            while not marker.exists() and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            assert marker.exists(), proc.communicate(timeout=60) if proc.poll() is not None else "기다리는 곳에 닿지 않음"
            proc.send_signal(getattr(signal, signame))
            if ignored:
                marker.unlink()  # 무시하는 신호라 그대로 기다리므로 풀어 준다
        out, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    return proc.returncode, out, err


@_POSIX_ONLY
@pytest.mark.parametrize("signame", ["SIGTERM", "SIGHUP", "SIGINT"])
def test_signal_after_commit_keeps_the_db_changes_says_so_and_leaves_no_temp_file(scratch_db, tmp_path, signame):
    """커밋한 뒤 이름을 바꾸기 전에 SIGTERM(kill 등)·SIGHUP·Ctrl+C(SIGINT)를 받으면 이름 바꾸기 실패와 같은 정리를
    거친다: 임시 파일은 지우고, 종료 코드 1과 함께 DB 변경은 커밋되어 남았고 CSV는 저장하지 못했다고 stderr 한 줄로 알린다.
    지난 실행의 CSV는 그대로다 (예전에는 SIGTERM·SIGHUP이면 임시 파일이 남고, Ctrl+C면 이 안내 없이 traceback만 나왔다)."""
    db = _base_schema_db(scratch_db)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / CSV_NAME).write_bytes(b"old csv")

    code, out, err = _run_and_signal(db, cwd, tmp_path / "held", "after_commit", signame)
    users = _users(db)
    badges = sum(count for _, _, _, count in users.values())
    assert len(users) == 45 and badges >= 45 * 5  # DB 변경은 커밋되어 남는다
    assert code == 1 and "완료" not in out
    assert err == (f"에러 발생: DB 변경(새로 만든 유저 45명, 배지 {badges}개)은 이미 커밋되어 그대로 남지만 "
                   f"CSV 파일({CSV_NAME})은 저장하지 못했습니다: 중단되었습니다(Ctrl+C 또는 종료 신호)\n")
    assert [p.name for p in cwd.iterdir()] == [CSV_NAME]  # 임시 파일은 남지 않는다
    assert (cwd / CSV_NAME).read_bytes() == b"old csv"


@_POSIX_ONLY
@pytest.mark.parametrize("signame", ["SIGTERM", "SIGHUP"])
def test_signal_before_commit_rolls_back_and_leaves_no_temp_file(scratch_db, tmp_path, signame):
    """CSV 임시 파일을 쓰는 중(커밋 전)에 SIGTERM·SIGHUP을 받으면 Ctrl+C처럼 DB는 롤백되고 쓰다 만 임시 파일은 지우며,
    종료 코드 1과 함께 어느 신호로 멈췄는지 stderr 한 줄로 남긴다 (예전에는 신호로 바로 죽어 임시 파일이 남았다)."""
    db = _base_schema_db(scratch_db)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / CSV_NAME).write_bytes(b"old csv")

    code, out, err = _run_and_signal(db, cwd, tmp_path / "held", "before_commit", signame)
    assert code == 1 and "완료" not in out
    assert err == f"에러 발생: {signame} 신호를 받아 중단했습니다.\n"
    assert _users(db) == {}  # 유저 45명도 남지 않는다
    with _connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM credentials").fetchone()[0] == 0
    assert [p.name for p in cwd.iterdir()] == [CSV_NAME]  # 쓰다 만 임시 파일도 없다
    assert (cwd / CSV_NAME).read_bytes() == b"old csv"


@_POSIX_ONLY
@pytest.mark.parametrize("signame", ["SIGTERM", "SIGHUP", "SIGINT"])
@pytest.mark.parametrize("where", ["in_commit", "in_close"])
def test_signal_right_after_the_commit_says_the_db_changes_were_kept(scratch_db, tmp_path, where, signame):
    """신호가 COMMIT 응답 직후(in_commit: 트랜잭션을 나가는 중)나 연결을 닫는 중(in_close)에 닿아도 커밋 뒤의 중단으로
    알린다: 종료 코드 1, DB 변경은 커밋되어 남았고 CSV는 저장하지 못했다는 stderr 한 줄, 임시 파일 없음, 지난 CSV는 그대로
    (예전에는 '신호를 받아 중단했습니다' 한 줄이나 traceback만 남아 커밋 전 중단처럼 보였다)."""
    db = _base_schema_db(scratch_db)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / CSV_NAME).write_bytes(b"old csv")

    code, out, err = _run_and_signal(db, cwd, tmp_path / "held", where, signame)
    users = _users(db)
    badges = sum(count for _, _, _, count in users.values())
    assert len(users) == 45 and badges >= 45 * 5  # DB 변경은 커밋되어 남는다
    assert code == 1 and "완료" not in out
    assert err == (f"에러 발생: DB 변경(새로 만든 유저 45명, 배지 {badges}개)은 이미 커밋되어 그대로 남지만 "
                   f"CSV 파일({CSV_NAME})은 저장하지 못했습니다: 중단되었습니다(Ctrl+C 또는 종료 신호)\n")
    assert [p.name for p in cwd.iterdir()] == [CSV_NAME]  # 임시 파일은 남지 않는다
    assert (cwd / CSV_NAME).read_bytes() == b"old csv"


@_POSIX_ONLY
@pytest.mark.parametrize("signame", ["SIGTERM", "SIGHUP", "SIGINT"])
def test_signal_in_the_finally_of_a_successful_run_exits_1_without_the_before_commit_line(scratch_db, tmp_path,
                                                                                          signame):
    """DB 변경·새 CSV·완료 안내까지 마친 뒤 finally의 정리 중(임시 파일 확인)에 신호가 닿아도 커밋 전 중단 안내('신호를 받아
    중단했습니다')나 traceback 없이 종료 코드 1로 끝난다 (예전에는 SIGTERM·SIGHUP이면 그 안내 한 줄, Ctrl+C면 traceback이 남아
    아무것도 저장되지 않은 것처럼 보였다)."""
    db = _base_schema_db(scratch_db)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / CSV_NAME).write_bytes(b"old csv")
    marker = tmp_path / "held"

    code, out, err = _run_and_signal(db, cwd, marker, "in_finally", signame)
    assert marker.read_text() == "x"  # 신호는 finally에서 한 번 닿았다
    assert (code, err) == (1, "")
    assert "완료! 총 45명의 유저(새로 만듦 45명" in out
    users = _users(db)
    assert len(users) == 45  # DB 변경은 커밋되었고
    _assert_only_the_csv_left(cwd)  # 새 CSV도 저장되었으며 임시 파일은 없다
    with (cwd / CSV_NAME).open(encoding="utf-8-sig", newline="") as f:
        assert {email for _, email, _, _ in list(csv.reader(f))[1:]} == set(users)


@_POSIX_ONLY
@pytest.mark.parametrize("signame", ["SIGTERM", "SIGHUP", "SIGINT"])
def test_signals_while_reporting_a_rename_failure_keep_the_db_kept_line_and_leave_no_temp_file(scratch_db, tmp_path,
                                                                                               signame):
    """커밋한 뒤 이름 바꾸기가 실패하고(엑셀이 연 CSV처럼 PermissionError) 그 안내를 남기기 직전과 안내 뒤 finally의 정리 중에
    신호가 한 번씩 닿아도, DB 변경은 남았고 CSV는 저장하지 못했다는 안내 한 줄(이유는 이름 바꾸기 오류)과 종료 코드 1로 끝나고
    임시 파일은 지운다 (예전에는 커밋 전 중단 안내나 traceback만 남거나, 두 번째 신호에 임시 파일이 남았다)."""
    db = _base_schema_db(scratch_db)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / CSV_NAME).write_bytes(b"old csv")
    marker = tmp_path / "held"

    code, out, err = _run_and_signal(db, cwd, marker, "in_rename_failure", signame)
    assert marker.read_text() == "xx"  # 안내 직전과 finally에서 한 번씩
    users = _users(db)
    badges = sum(count for _, _, _, count in users.values())
    assert len(users) == 45 and badges >= 45 * 5  # DB 변경은 커밋되어 남는다
    assert code == 1 and "완료" not in out
    assert err == (f"에러 발생: DB 변경(새로 만든 유저 45명, 배지 {badges}개)은 이미 커밋되어 그대로 남지만 "
                   f"CSV 파일({CSV_NAME})은 저장하지 못했습니다: [Errno 13] 엑셀이 연 파일이라 바꿀 수 없음(테스트)\n")
    assert [p.name for p in cwd.iterdir()] == [CSV_NAME]  # 임시 파일은 남지 않는다
    assert (cwd / CSV_NAME).read_bytes() == b"old csv"


@_POSIX_ONLY
@pytest.mark.parametrize("signame", ["SIGTERM", "SIGHUP", "SIGINT"])
def test_second_signal_while_cleaning_up_before_the_commit_leaves_no_temp_file(scratch_db, tmp_path, signame):
    """커밋 전 CSV 임시 파일을 쓰는 중에 멈춘 뒤 그 정리(finally) 중에 두 번째 신호가 닿아도 쓰다 만 임시 파일은 지우고, 첫 신호의
    결과(SIGTERM·SIGHUP은 '신호를 받아 중단했습니다' 한 줄과 종료 코드 1, Ctrl+C는 KeyboardInterrupt traceback)만 남는다.
    DB는 롤백된다 (예전에는 두 번째 신호가 정리를 끊어 임시 파일이 남았다)."""
    db = _base_schema_db(scratch_db)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / CSV_NAME).write_bytes(b"old csv")
    marker = tmp_path / "held"

    code, out, err = _run_and_signal(db, cwd, marker, "twice_before_commit", signame)
    assert marker.read_text() == "xx"  # CSV를 쓰는 중과 finally에서 한 번씩
    assert "완료" not in out
    if signame == "SIGINT":
        assert code not in (0, 1) and err.rstrip().endswith("KeyboardInterrupt") and "DB 변경" not in err
    else:
        assert (code, err) == (1, f"에러 발생: {signame} 신호를 받아 중단했습니다.\n")
    assert _users(db) == {}  # 유저 45명도 남지 않는다
    with _connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM credentials").fetchone()[0] == 0
    assert [p.name for p in cwd.iterdir()] == [CSV_NAME]  # 쓰다 만 임시 파일도 없다
    assert (cwd / CSV_NAME).read_bytes() == b"old csv"


@_POSIX_ONLY
@pytest.mark.parametrize("signame", ["SIGHUP", "SIGTERM"])
def test_inherited_ignored_signal_stays_ignored_and_the_run_completes(scratch_db, tmp_path, signame):
    """nohup처럼 그 신호를 무시하도록 물려받아 실행하면(preexec_fn으로 SIG_IGN) 실행 중에 그 신호(터미널·SSH 세션 종료의
    SIGHUP 등)가 와도 예전처럼 무시하고 끝까지 실행한다: 종료 코드 0, 유저 45명, 새 CSV (예전에는 처리기를 덮어써서 중단·롤백)."""
    db = _base_schema_db(scratch_db)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / CSV_NAME).write_bytes(b"old csv")

    code, out, err = _run_and_signal(db, cwd, tmp_path / "held", "before_commit", signame, ignored=signame)
    assert (code, err) == (0, "") and "새로 만듦 45명" in out
    users = _users(db)
    assert len(users) == 45
    _assert_only_the_csv_left(cwd)
    with (cwd / CSV_NAME).open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))[1:]
    assert {email for _, email, _, _ in rows} == set(users)


@pytest.mark.parametrize("stop", ["signal", "ctrl_c"])
@pytest.mark.parametrize("where", ["rename", "print"])
def test_stop_right_after_the_rename_does_not_say_the_csv_was_not_saved(scratch_db, monkeypatch, capsys, tmp_path,
                                                                        where, stop):
    """중단이 이름 바꾸기 바로 뒤(rename: os.replace는 끝남)나 완료 안내를 출력하는 중(print)에 닿으면 DB 변경도 CSV도 이미
    저장되었으므로 'CSV는 저장하지 못했습니다'도, 커밋 전 중단처럼 보이는 '신호를 받아 중단했습니다' 줄·traceback도 내지 않고
    종료 코드 1로 끝난다 (stop: signal은 SIGTERM 처리기의 중단, ctrl_c는 Ctrl+C처럼 except Exception에 잡히지 않는 중단)."""
    import generate_test_data

    db = _base_schema_db(scratch_db)
    real_replace = os.replace

    def stopped():
        return SystemExit("에러 발생: SIGTERM 신호를 받아 중단했습니다.") if stop == "signal" else _Interrupted()

    if where == "rename":
        def replace_then_stop(src, dst, *args, **kwargs):
            real_replace(src, dst, *args, **kwargs)
            if os.fspath(dst) == CSV_NAME:
                raise stopped()

        monkeypatch.setattr(generate_test_data.os, "replace", replace_then_stop)
    else:
        def print_then_stop(*args, **kwargs):
            if args and str(args[0]).startswith("완료"):
                raise stopped()
            return builtins.print(*args, **kwargs)

        monkeypatch.setattr(generate_test_data, "print", print_then_stop, raising=False)
    code, out, err, rows = _run(generate_test_data, db, monkeypatch, capsys, tmp_path)
    assert (code, err) == (1, "") and "완료" not in out
    assert len(_users(db)) == 45  # DB 변경은 커밋되었고
    assert rows is not None and len(rows) == 45  # 새 CSV도 저장되었다
    _assert_only_the_csv_left(tmp_path)
