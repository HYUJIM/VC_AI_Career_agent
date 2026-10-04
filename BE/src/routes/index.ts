import { Router } from 'express';
import { verifyCredential } from '../controllers/verify.controller';
import { getUserProfileAndCareers } from '../controllers/user.controller';

const router = Router();

// 1. 검증 엔드포인트 (AI가 제출한 토큰을 받아 DXWorks API를 호출하고 DB에 저장)
router.post('/verify', verifyCredential);

// 2. 특정 사용자의 파싱된 신원 및 수료증/이력 데이터를 AI에게 제공
router.get('/subjects/:did/careers', getUserProfileAndCareers);

export default router;
