import { Request, Response } from 'express';
import { dbService } from '../services/db.service';

/**
 * AI 모델 전처리용 데이터 서빙 엔드포인트
 */
export const getUserProfileAndCareers = async (req: Request, res: Response): Promise<void> => {
  try {
    const did = req.params.did as string;

    const data = await dbService.getUserAndCareers(did);

    if (!data) {
      res.status(404).json({ error: 'User not found or not verified yet.' });
      return;
    }

    // AI에게 전달할 포맷 그대로 응답
    res.json(data);
  } catch (error: any) {
    res.status(500).json({ error: 'Internal Server Error', details: error.message });
  }
};
