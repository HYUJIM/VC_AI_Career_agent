"""외부 응답 본문을 상한 안에서만 모은다 (순수 로직, I/O 없음. 동기·비동기 코드가 함께 쓴다).

`Accept-Encoding: identity`로 압축을 요청하지 않아도 상대(또는 중간 CDN)가 gzip·deflate로 보낼 수 있다. httpx에 압축 해제를
맡기면 조각 하나를 통째로 풀고 나서야 크기를 볼 수 있어, 수백 KiB짜리 본문이 수백 MiB로 풀린다. 그래서 전송된 그대로의
조각(httpx의 aiter_raw / iter_raw)을 받아 zlib으로 직접, 남은 상한만큼만 푼다.

    body = CappedBody(response.headers.get("Content-Encoding", ""), limit)   # 받지 않는 방식이면 UnsupportedEncoding
    for chunk in response.iter_raw():                                        # 비동기는 async for ... aiter_raw()
        body.feed(chunk)                                                     # 상한을 넘으면 BodyTooLarge, 깨진 압축은 zlib.error
    content = body.finish()                                                  # 압축이 중간에 끊겼으면 zlib.error

- 전송된 바이트 수와 풀어낸 바이트 수를 각각 limit까지만 받는다.
- 받는 방식: 없음·identity(그대로), gzip·x-gzip·deflate(zlib으로 푼다. gzip 머리말과 zlib 머리말을 자동으로 가린다).
  그 밖의 값(br·zstd, `gzip, gzip`처럼 겹친 압축, 모르는 이름)은 받지 않는다.
"""
import zlib

_PLAIN = ("", "identity")
_INFLATED = ("gzip", "x-gzip", "deflate")


class UnsupportedEncoding(Exception):
    """받지 않는 Content-Encoding (본문은 읽지 않는다)."""


class BodyTooLarge(Exception):
    """전송된 바이트 또는 풀어낸 바이트가 상한을 넘었다."""


class CappedBody:
    def __init__(self, content_encoding: str, limit: int):
        coding = content_encoding.strip().lower()
        if coding in _PLAIN:
            self._inflater = None
        elif coding in _INFLATED:
            self._inflater = zlib.decompressobj(zlib.MAX_WBITS | 32)  # gzip·zlib 머리말 자동 판별
        else:
            raise UnsupportedEncoding(coding)
        self.limit = limit
        self._wire = 0  # 전송된 바이트 수
        self._size = 0  # 풀어낸 바이트 수
        self._chunks: list[bytes] = []

    def feed(self, chunk: bytes) -> None:
        """전송된 그대로의 조각 하나를 받는다."""
        self._wire += len(chunk)
        if self._wire > self.limit:
            raise BodyTooLarge()
        if self._inflater is not None:
            # 남은 상한보다 1바이트만 더 풀어 본다: 그만큼 나오거나 풀 입력이 남으면 상한을 넘는 본문이다.
            chunk = self._inflater.decompress(chunk, self.limit + 1 - self._size)
            if self._inflater.unconsumed_tail:
                raise BodyTooLarge()
        self._size += len(chunk)
        if self._size > self.limit:
            raise BodyTooLarge()
        self._chunks.append(chunk)

    def finish(self) -> bytes:
        """모은 본문. 압축 본문이 끝까지 오지 않았으면 zlib.error."""
        if self._inflater is not None and not self._inflater.eof:
            raise zlib.error("incomplete compressed body")
        return b"".join(self._chunks)
