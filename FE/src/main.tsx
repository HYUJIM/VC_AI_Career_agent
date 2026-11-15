// 가장 먼저 import 한다: 스키마가 만들어지기 전에 zod 설정을 바꿔야 한다 (lib/zodConfig.ts)
import './lib/zodConfig'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MotionConfig } from 'motion/react'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App.tsx'
import './index.css'
import { isNoResponseError } from './lib/apiClient'
import { useAuthStore } from './store/authStore'

// Pretendard 웹폰트(CDN 스타일시트)는 index.html 의 <link> 대신 여기서 스크립트로 붙인다.
// HTML 에 적힌 스타일시트는 CDN 이 응답할 때까지 첫 화면을 막지만(느리거나 막힌 망에서는 계속 흰 화면),
// 스크립트로 넣은 스타일시트는 막지 않는다. 기본 글꼴로 먼저 그리고 폰트가 도착하면 바뀐다.
const fontLink = document.createElement('link')
fontLink.rel = 'stylesheet'
fontLink.crossOrigin = 'anonymous'
fontLink.href =
  'https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css'
document.head.appendChild(fontLink)

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 모든 조회의 기본값: BE의 응답을 전혀 받지 못했을 때(연결 실패·시간 초과)만 한 번 자동으로 다시 시도한다.
      // BE가 답한 오류(404·429, 혼잡 503 + Retry-After, 502)는 바로 다시 불러도 같고, 혼잡할 때 요청만 두 배가 된다.
      // 그런 경우에는 오류 안내에 있는 "다시 시도" 버튼으로 사용자가 직접 다시 부른다 (그 조회를 다시 부르는 동안에는 버튼이 잠긴다).
      // 오류를 화면에 보여 주는 조회에는 모두 이 버튼이 있다 (자격증명 목록, 포트폴리오 목록·상세, 공유 링크 목록, 공고 목록, 직무별 통계 목록·상세,
      // 기업용 조회). 버튼 대신 다른 안내가 나오는 것은 자격증명 목록 403·404("다시 로그인")와 기업용 조회 404(새 링크 요청)뿐이다.
      // 그 밖의 오류에는 다시 불러도 같은 오류(직무별 통계 404·409, 포트폴리오 상세 404 등)라도 버튼이 나온다. 401 은 로그아웃되어 로그인 화면으로 간다.
      // 오류를 보여 주지 않는 조회(기술 이름 GET /skills, 프로필 GET /users/me)는 그 화면·메뉴를 다시 열 때 다시 부른다.
      // retry: false 로 둔 조회는 그대로 다시 시도하지 않는다.
      retry: (count, err) => isNoResponseError(err) && count < 1,
      refetchOnWindowFocus: false,
    },
  },
})

// 로그인한 사용자가 바뀌면(로그아웃·토큰 만료·다른 계정 로그인·다른 탭의 변경 반영) 조회·요청 캐시를 모두 버린다.
// ['portfolios']·['shares']·['portfolio', id] 같은 캐시 키에는 사용자가 들어 있지 않아, 버리지 않으면 같은 탭에서
// 이어서 로그인한 사람에게 이전 사용자의 포트폴리오·공유 링크가 보인다 (목록 호출이 느리거나 실패하면 계속 남는다).
useAuthStore.subscribe((state, prev) => {
  if (state.user?.id !== prev.user?.id) queryClient.clear()
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        {/* OS의 "동작 줄이기" 설정을 존중한다 (transform 애니메이션 생략) */}
        <MotionConfig reducedMotion="user">
          <App />
        </MotionConfig>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
