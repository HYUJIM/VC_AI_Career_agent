import { apiClient } from '@/lib/apiClient';
import { isPreviewSession, previewMe } from '@/lib/preview';
import type { LoginResponse, MeResponse, RegisterResponse } from '@/types/api';

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

/** GET /api/v1/users/me (v0.5.0) — 토큰의 사용자 프로필 + 지갑(홀더 키) 상태 */
export async function getMe(): Promise<MeResponse> {
  if (isPreviewSession()) return previewMe();
  const { data } = await apiClient.get<MeResponse>('/v1/users/me');
  return data;
}
