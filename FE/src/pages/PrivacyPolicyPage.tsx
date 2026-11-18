import { CollectionTable, TransferTable } from '@/components/PrivacyConsent';
import { Card } from '@/components/ui/Card';
import { PRIVACY_POLICY_VERSION } from '@/lib/privacy';

const GITHUB_ISSUES_URL = 'https://github.com/HYUJIM/VC_AI_Career_agent/issues';

/** 개인정보 처리 안내 (로그인 없이 볼 수 있음, /privacy) */
export function PrivacyPolicyPage() {
  return (
    <div className="flex justify-center">
      <Card className="flex w-full max-w-3xl flex-col gap-5 p-8 text-[14px] leading-relaxed text-[var(--color-text-sub)]">
        <div>
          <h1 className="text-[24px] font-bold leading-snug tracking-tight text-[var(--color-text)]">개인정보 처리 안내</h1>
          <p className="mt-1 text-[13px] text-[var(--color-text-muted)]">시행일 {PRIVACY_POLICY_VERSION}</p>
        </div>
        <p>
          커리어 지갑(VC AI Career Agent)은 검증 가능한 자격증명(VC)으로 직무 비교·학습 계획·포트폴리오를 제공하기 위해 아래와
          같이 개인정보를 처리해요.
        </p>

        <section className="flex flex-col gap-2">
          <h2 className="text-[16px] font-bold text-[var(--color-text)]">1. 수집·이용하는 개인정보와 보유 기간</h2>
          <CollectionTable />
        </section>

        <section className="flex flex-col gap-2">
          <h2 className="text-[16px] font-bold text-[var(--color-text)]">2. 처리 위탁·외부 전송</h2>
          <TransferTable />
          <p>
            Anthropic(미국)으로의 전송은 사용자가 LLM 학습 계획·AI 포트폴리오 화면에서 따로 동의할 때만 일어나요. 보내는 정보는
            표에 적은 것뿐이고, 이름·이메일·DID와 자격증명 원본은 보내지 않아요. 전달된 정보는 Anthropic의 정책에 따라
            처리·보관돼요.
          </p>
        </section>

        <section className="flex flex-col gap-1">
          <h2 className="text-[16px] font-bold text-[var(--color-text)]">3. 안전하게 지키는 방법</h2>
          <ul className="list-disc pl-5">
            <li>비밀번호는 되돌릴 수 없는 해시로만 저장하고, 지갑 서명 키는 암호화해 보관해요.</li>
            <li>모든 접속은 HTTPS로 암호화하고, 데이터베이스 연결도 암호화해요.</li>
            <li>자격증명은 쓸 때마다 서명 원본을 다시 검증하고, 공유 링크에는 사용자가 고른 자격증명만 보여요.</li>
          </ul>
        </section>

        <section className="flex flex-col gap-1">
          <h2 className="text-[16px] font-bold text-[var(--color-text)]">4. 파기</h2>
          <p>
            보유 기간이 끝나거나 탈퇴를 요청하면 데이터베이스와 백업에서 지체 없이 지워요. 사용자가 직접 지운 포트폴리오·공유
            링크는 바로 지워져요. 서비스를 끝낼 때는 모든 개인정보를 지워요.
          </p>
        </section>

        <section className="flex flex-col gap-1">
          <h2 className="text-[16px] font-bold text-[var(--color-text)]">5. 이용자의 권리</h2>
          <p>
            내 개인정보의 열람·정정·삭제(탈퇴)와 처리 정지를 요청할 수 있어요. 동의는 거부할 수 있지만, 필수 항목에 동의하지 않으면
            가입할 수 없어요. 만 14세 미만은 가입할 수 없어요.
          </p>
        </section>

        <section className="flex flex-col gap-1">
          <h2 className="text-[16px] font-bold text-[var(--color-text)]">6. 문의</h2>
          <p>
            개인정보 관련 요청과 문의는{' '}
            <a
              href={GITHUB_ISSUES_URL}
              target="_blank"
              rel="noreferrer"
              className="font-semibold text-[var(--color-primary)] hover:underline"
            >
              문의 · 버그 제보
            </a>
            로 남겨 주세요. 공개 게시판이므로 비밀번호·자격증명 원본 같은 민감한 정보는 적지 마세요.
          </p>
        </section>
      </Card>
    </div>
  );
}
