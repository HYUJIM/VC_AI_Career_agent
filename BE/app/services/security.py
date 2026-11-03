import bcrypt

# bcrypt는 입력의 앞 72바이트만 사용하므로 그보다 긴 비밀번호는 받지 않는다.
MAX_PASSWORD_BYTES = 72

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # 해시 형식이 깨져 있거나 비밀번호가 72바이트를 넘는 경우
        return False
