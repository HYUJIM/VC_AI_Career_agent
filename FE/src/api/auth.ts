import { apiClient } from '@/lib/apiClient';
import type { LoginResponse, RegisterResponse } from '@/types/api';

export interface RegisterPayload {
  name: string;
  email: string;
  password: string;
}

export interface LoginPayload {
  email: string;
  password: string;
}

/** POST /api/v1/users/register */
export async function registerUser(payload: RegisterPayload): Promise<RegisterResponse> {
  const { data } = await apiClient.post<RegisterResponse>('/v1/users/register', payload);
  return data;
}

/** POST /api/v1/users/login */
export async function loginUser(payload: LoginPayload): Promise<LoginResponse> {
  const { data } = await apiClient.post<LoginResponse>('/v1/users/login', payload);
  return data;
}

/**
 * POST /api/v1/users/logout (204): 이 계정에 발급된 액세스 토큰을 서버에서 모두 무효로 만든다 (다른 기기 포함).
 * 화면 로그아웃이 스토어의 토큰을 먼저 지우므로 토큰을 직접 받아 헤더에 싣는다.
 */
export async function logoutUser(accessToken: string): Promise<void> {
  await apiClient.post('/v1/users/logout', undefined, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
}
