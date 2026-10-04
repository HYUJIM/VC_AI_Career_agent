import fs from 'fs';
import path from 'path';

export interface IUser {
  user_id: string; // did:key:...
  name: string;
  student_id: string;
  role: string;
  is_verified: boolean;
}

export interface ICareer {
  badge_id: string;
  user_id: string; // fk
  issuer_name: string;
  badge_name: string;
  description: string;
  criteria: string;
  status: string;
  verified_at: string;
}

const dbPath = path.join(__dirname, '../../mock-db.json');
console.log('DB PATH IS:', dbPath);
console.log('__dirname IS:', __dirname);

function readDB() {
  if (!fs.existsSync(dbPath)) return { usersDB: {}, careersDB: {} };
  return JSON.parse(fs.readFileSync(dbPath, 'utf8'));
}

function writeDB(data: any) {
  fs.writeFileSync(dbPath, JSON.stringify(data, null, 2));
}

export const dbService = {
  async saveUser(user: IUser) {
    const db = readDB();
    db.usersDB[user.user_id] = user;
    writeDB(db);
    return user;
  },

  async saveCareer(career: ICareer) {
    const db = readDB();
    db.careersDB[career.badge_id] = career;
    writeDB(db);
    return career;
  },

  async getUserAndCareers(userDid: string) {
    const db = readDB();
    const careers = Object.values(db.careersDB).filter((c: any) => c.user_id === userDid) as ICareer[];
    const user = db.usersDB[userDid] as IUser | undefined;

    if (!user && careers.length === 0) return null;

    return {
      user_profile: {
        did: userDid,
        name: user?.name || 'Unknown (신원 미인증)',
        student_id: user?.student_id || '',
        role: user?.role || ''
      },
      verified_careers: careers.map((c) => ({
        credential_id: c.badge_id,
        issuer: c.issuer_name,
        badge_name: c.badge_name,
        description: c.description,
        criteria: c.criteria,
        status: c.status,
        verified_at: c.verified_at
      }))
    };
  }
};
