/**
 * 임시 DB 서비스 (Mock)
 * DB ERD 및 실제 연동(ex: TypeORM, Prisma 등) 작업은 다른 담당자가 진행할 예정이므로,
 * 여기서는 데이터 저장 및 조회를 시뮬레이션하는 인터페이스만 구성합니다.
 */
export interface IUser {
    user_id: string;
    name: string;
    student_id: string;
    role: string;
    is_verified: boolean;
}
export interface ICareer {
    badge_id: string;
    user_id: string;
    issuer_name: string;
    badge_name: string;
    description: string;
    criteria: string;
    status: string;
    verified_at: string;
}
export declare const dbService: {
    saveUser(user: IUser): Promise<IUser>;
    saveCareer(career: ICareer): Promise<ICareer>;
    getUserAndCareers(userDid: string): Promise<{
        user_profile: {
            did: string;
            name: string;
            student_id: string;
            role: string;
        };
        verified_careers: {
            credential_id: string;
            issuer: string;
            badge_name: string;
            description: string;
            criteria: string;
            status: string;
            verified_at: string;
        }[];
    } | null>;
};
//# sourceMappingURL=db.service.d.ts.map