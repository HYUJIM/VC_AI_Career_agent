import express from 'express';
import cors from 'cors';
import dotenv from 'dotenv';
import apiRoutes from './routes';
import mockIssuerRouter from './mock-issuer/issuer.routes';

dotenv.config();

const app = express();
const port = process.env.PORT || 3000;

app.use(cors());
app.use(express.json());

// Health Check
app.get('/health', (req, res) => {
  res.json({
    status: 'ok',
    message: 'VC AI Career Agent BE is running'
  });
});

// Main API Routes
app.use('/api/v1', apiRoutes);

// Mock Issuer Routes (논문 테스트용 임시 발급 서버)
app.use('/api/mock-issuer', mockIssuerRouter);

app.listen(port, () => {
  console.log(`🚀 Server running on port ${port}`);
});
