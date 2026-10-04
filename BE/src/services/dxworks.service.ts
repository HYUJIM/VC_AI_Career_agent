import axios from 'axios';
import dotenv from 'dotenv';

dotenv.config();

const API_HOST = process.env.DXWORKS_API_HOST || 'https://api.dxworks.kr';
// 주의: 발급/검증 서버 호출에 사용되는 서비스 계정 키. 프론트에 노출되면 안 됩니다.
const API_KEY = process.env.DXWORKS_API_KEY || 'test_key'; 

export const dxWorksService = {
  /**
   * OID4VP 1-shot (Identity) 검증
   * @param vpToken 프론트에서 넘어온 vpToken (SD-JWT~KB-JWT)
   */
  async verifyIdentity(vpToken: string) {
    try {
      const response = await axios.post(
        `${API_HOST}/api/v1/verifier/holder-presentations/verify`,
        { vpToken },
        {
          headers: {
            'Authorization': `Bearer ${API_KEY}`,
            'Content-Type': 'application/json'
          }
        }
      );
      return response.data; 
    } catch (error: any) {
      console.error('DXWorks Identity Verify Error:', error.response?.data || error.message);
      throw error;
    }
  },

  /**
   * OB3 (Badge) 검증
   * @param vcJwt 프론트에서 넘어온 OB3 배지 VC-JWT 토큰
   */
  async verifyBadge(vcJwt: string) {
    try {
      // OB3 검증 엔드포인트는 공개 엔드포인트이므로 Auth 헤더가 없어도 됩니다. (문서 참고)
      const response = await axios.post(
        `${API_HOST}/api/v1/verifier/ob3/verify`,
        { vcJwt },
        {
          headers: {
            'Content-Type': 'application/json'
          }
        }
      );
      return response.data;
    } catch (error: any) {
      console.error('DXWorks Badge Verify Error:', error.response?.data || error.message);
      throw error;
    }
  }
};
