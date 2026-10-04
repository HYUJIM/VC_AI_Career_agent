import { Request, Response } from 'express';
import { dxWorksService } from '../services/dxworks.service';
import { dbService } from '../services/db.service';

/**
 * 단순 JWT 페이로드 디코딩 유틸
 */
function decodeJwtPayload(token: string) {
  try {
    const payloadBase64 = token.split('.')[1];
    if (!payloadBase64) return null;
    const jsonStr = Buffer.from(payloadBase64, 'base64').toString('utf8');
    return JSON.parse(jsonStr);
  } catch (e) {
    return null;
  }
}

export const verifyCredential = async (req: Request, res: Response): Promise<void> => {
  try {
    const { type, token } = req.body; 
    // type: 'identity' | 'badge'
    // token: vpToken(Identity) or vcJwt(Badge)

    if (!type || !token) {
      res.status(400).json({ error: 'type and token are required' });
      return;
    }

    if (type === 'identity') {
      // 1. Identity 검증
      const verifyResult = await dxWorksService.verifyIdentity(token);

      // 응답 구조: { verified: true, holderDid: '...', claims: { name, memberId, role } }
      if (verifyResult.verified && verifyResult.claims) {
        const claims = verifyResult.claims;
        
        await dbService.saveUser({
          user_id: verifyResult.holderDid,
          name: claims.name || '',
          student_id: claims.memberId || '',
          role: claims.role || '',
          is_verified: true
        });

        res.json({ success: true, message: 'Identity verified and saved.', data: verifyResult });
      } else {
        res.status(400).json({ success: false, message: 'Identity verification failed.', data: verifyResult });
      }
    } else if (type === 'badge') {
      // 2. OB3 Badge 검증
      const verifyResult = await dxWorksService.verifyBadge(token);

      // 응답 구조: { valid: true, checks: { recipient: {subjectId}, issuer: {...}, credentialId: {...} } }
      if (verifyResult.valid && verifyResult.checks) {
        const checks = verifyResult.checks;

        // VC-JWT 페이로드에서 배지 상세 내용 추출 (description, criteria 등)
        const payload = decodeJwtPayload(token);
        const vc = payload?.vc || payload || {};
        const credentialSubject = vc.credentialSubject || {};
        const achievement = credentialSubject.achievement || {};
        
        await dbService.saveCareer({
          badge_id: checks.credentialId?.id || vc.id || 'unknown_id',
          user_id: checks.recipient?.subjectId || 'unknown_did',
          issuer_name: checks.issuer?.issuerName || vc.issuer?.name || 'Unknown Issuer',
          badge_name: verifyResult.badge?.name || achievement.name || vc.name || 'Unknown Badge',
          description: achievement.description || vc.description || '',
          criteria: achievement.criteria?.narrative || vc.criteria?.narrative || '',
          status: checks.credentialId?.credentialStatus || 'VALID',
          verified_at: new Date().toISOString()
        });

        res.json({ success: true, message: 'Badge verified and saved.', data: verifyResult });
      } else {
        res.status(400).json({ success: false, message: 'Badge verification failed.', data: verifyResult });
      }
    } else {
      res.status(400).json({ error: 'Invalid type. Use "identity" or "badge"' });
    }
  } catch (error: any) {
    res.status(500).json({ error: 'Internal Server Error', details: error.message });
  }
};
