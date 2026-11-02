import { apiClient } from '@/lib/apiClient';
import type { LoginResponse, RegisterResponse } from '@/types/api';

export interface RegisterPayload {
  name: string;
  email: string;
}

export interface LoginPayload {
  email: string;
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
