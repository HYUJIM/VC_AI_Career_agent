import { Router } from 'express';
import { issueMockBadge } from './issuer.controller';

const mockIssuerRouter = Router();

// POST /api/mock-issuer/issue
mockIssuerRouter.post('/issue', issueMockBadge);

export default mockIssuerRouter;
