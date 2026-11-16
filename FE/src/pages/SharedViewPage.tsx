import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { getPublicShare } from '@/api/shares';
import { SharedCredentialCard } from '@/components/SharedCredentialCard';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { getApiErrorKind, getApiErrorMessage } from '@/lib/apiClient';

const formatDate = (iso: string) => new Date(iso).toLocaleDateString('ko-KR');

/**
 * 기업용 조회 화면 (BE v0.6.0 GET /public/shares/{token}, 로그인 불필요).
 * 열 때마다 BE가 서명 원본을 다시 검증하므로 지금 시점의 상태를 보여준다.
 */
export function SharedViewPage() {
  const { token = '' } = useParams<{ token: string }>();
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['publicShare', token],
    queryFn: () => getPublicShare(token),
    enabled: Boolean(token),
    // 자동 재시도는 앱 기본값(main.tsx)을 따른다: BE의 응답을 전혀 받지 못했을 때만 한 번.
    // BE가 답한 오류(404·429, 검증 혼잡 503 + Retry-After, 502)는 아래 "다시 시도" 버튼으로 사용자가 직접 다시 부른다.
    // 버튼은 다시 부르는 중에는 잠근다 (누를 때마다 같은 GET 과 서명 재검증이 또 나가지 않게).
  });

  if (isLoading) {
    return <p className="px-1 text-[15px] text-[var(--color-text-muted)]">자격증명을 검증하는 중...</p>;
  }

  if (isError || !data) {
    const kind = getApiErrorKind(error);
    return (
      <div className="mx-auto flex max-w-2xl flex-col gap-4">
        {kind === 'notFound' ? (
          <Alert variant="warning" title="열 수 없는 링크입니다.">
            링크가 없거나 철회·만료되었습니다. 링크를 보낸 분께 새 링크를 요청해주세요.
          </Alert>
        ) : kind === 'rateLimited' ? (
          <Alert
            variant="warning"
            title="조회 요청이 너무 많습니다."
            actions={
              <Button variant="outline" size="sm" disabled={isFetching} onClick={() => refetch()}>
                다시 시도
              </Button>
            }
          >
            잠시 후 다시 열어주세요.
          </Alert>
        ) : (
          <Alert
            variant="error"
            title="자격증명을 불러오지 못했습니다."
            actions={
              <Button variant="outline" size="sm" disabled={isFetching} onClick={() => refetch()}>
                다시 시도
              </Button>
            }
          >
            {getApiErrorMessage(error)}
          </Alert>
        )}
      </div>
    );
  }

  const verifiedCount = data.credentials.filter((c) => c.verified).length;

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-4">
      <section className="flex flex-col gap-2 px-1">
        {/* 이름은 계정 주인이 입력한 값이다. <bdi> 로 감싸 방향 전환 문자(U+202E 등)가 제목의 나머지 글자 순서를 바꾸지 못하게 한다 */}
        <h1 className="text-[26px] font-bold leading-snug tracking-tight">
          <bdi>{data.holder.name}</bdi> 님의 검증된 자격증명
        </h1>
        <p className="text-[15px] leading-relaxed text-[var(--color-text-sub)]">
          {new Date(data.verified_at).toLocaleString('ko-KR')}에 서명 원본을 다시 검증했어요. 총{' '}
          {data.credentials.length}개 중 {verifiedCount}개가 지금 유효합니다.
        </p>
        {data.holder.did && (
          <p className="font-mono-did break-all text-[12px] text-[var(--color-text-muted)]">{data.holder.did}</p>
        )}
        <p className="text-[12px] text-[var(--color-text-muted)]">이 링크는 {formatDate(data.expires_at)}까지 열 수 있어요.</p>
      </section>

      {data.credentials.length === 0 ? (
        <Card>
          <p className="text-[15px] text-[var(--color-text-muted)]">공개된 자격증명이 없어요.</p>
        </Card>
      ) : (
        data.credentials.map((c) => <SharedCredentialCard key={c.record_id} credential={c} />)
      )}
    </div>
  );
}
