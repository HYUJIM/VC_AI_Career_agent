import { Navigate, Route, Routes } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { GuestRoute, ProtectedRoute } from '@/components/ProtectedRoute';
import { CareerPlanPage } from '@/pages/CareerPlanPage';
import { DashboardPage } from '@/pages/DashboardPage';
import { IssueCredentialPage } from '@/pages/IssueCredentialPage';
import { OAuthCallbackPage } from '@/pages/OAuthCallbackPage';
import { PortfolioBuilderPage } from '@/pages/PortfolioBuilderPage';
import { LoginPage } from '@/pages/LoginPage';
import { RegisterPage } from '@/pages/RegisterPage';
import { SharedViewPage } from '@/pages/SharedViewPage';
import { OAUTH_PROVIDER_IDS, OAUTH_PROVIDERS } from '@/lib/oauthProviders';

function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route
          path="/login"
          element={
            <GuestRoute>
              <LoginPage />
            </GuestRoute>
          }
        />
        <Route
          path="/register"
          element={
            <GuestRoute>
              <RegisterPage />
            </GuestRoute>
          }
        />
        {/* 소셜 로그인 콜백 (/auth/google/callback, /auth/kakao/callback, /auth/naver/callback).
            key: 다른 제공자의 콜백으로 바로 옮겨 가도 화면 상태를 새로 시작한다 */}
        {OAUTH_PROVIDER_IDS.map((provider) => (
          <Route
            key={provider}
            path={OAUTH_PROVIDERS[provider].callbackPath}
            element={<OAuthCallbackPage key={provider} provider={provider} />}
          />
        ))}
        {/* 기업용 공유 링크 조회 (로그인 불필요, BE share_path = /share/{token}) */}
        <Route path="/share/:token" element={<SharedViewPage />} />
        <Route
          path="/dashboard"
          element={
            <ProtectedRoute>
              <DashboardPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/issue"
          element={
            <ProtectedRoute>
              <IssueCredentialPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/portfolio"
          element={
            <ProtectedRoute>
              <PortfolioBuilderPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/career"
          element={
            <ProtectedRoute>
              <CareerPlanPage />
            </ProtectedRoute>
          }
        />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Route>
    </Routes>
  );
}

export default App;
