import { Request, Response } from 'express';
import axios from 'axios';
import dotenv from 'dotenv';

dotenv.config();

const API_HOST = process.env.DXWORKS_API_HOST || 'https://api.dxworks.kr';
const API_KEY = process.env.DXWORKS_API_KEY;

/**
 * 논문/테스트용 임시 배지 발급 API
 * 프론트나 스크립트에서 호출하면, DXWorks API를 대신 찔러서
 * 진짜 서명이 들어간 배지(VC-JWT)를 발급받아 반환합니다.
 */
export const issueMockBadge = async (req: Request, res: Response): Promise<void> => {
  try {
    const { templateId, holderDid } = req.body;

    if (!templateId || !holderDid) {
      res.status(400).json({ error: 'templateId and holderDid are required' });
      return;
    }

    if (!API_KEY || API_KEY === 'YOUR_API_KEY_HERE') {
      res.status(500).json({ error: 'DXWORKS_API_KEY is not configured in .env' });
      return;
    }

    console.log(`[Mock Issuer] Issuing badge (template: ${templateId}) for ${holderDid}`);

    // DXWorks 기관 API 호출 (발급 기관 역할)
    const response = await axios.post(
      `${API_HOST}/api/v1/issuer/ob3/credentials`,
      { templateId, holderDid },
      {
        headers: {
          'Authorization': `Bearer ${API_KEY}`,
          'Content-Type': 'application/json'
        }
      }
    );

    // DXWorks 응답에서 순수 JWT 문자열만 추출
    const responseData = response.data;
    const jwtString = responseData.data?.jwt || responseData.jwt || responseData;

    res.status(201).json({
      success: true,
      message: 'Successfully issued a mock badge',
      vcJwt: jwtString
    });

  } catch (error: any) {
    console.error('[Mock Issuer Error]', error.response?.data || error.message);
    res.status(500).json({
      error: 'Failed to issue mock credential',
      details: error.response?.data || error.message
    });
  }
};
