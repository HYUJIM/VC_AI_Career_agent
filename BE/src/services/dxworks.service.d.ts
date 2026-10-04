export declare const dxWorksService: {
    /**
     * OID4VP 1-shot (Identity) 검증
     * @param vpToken 프론트에서 넘어온 vpToken (SD-JWT~KB-JWT)
     */
    verifyIdentity(vpToken: string): Promise<any>;
    /**
     * OB3 (Badge) 검증
     * @param vcJwt 프론트에서 넘어온 OB3 배지 VC-JWT 토큰
     */
    verifyBadge(vcJwt: string): Promise<any>;
};
//# sourceMappingURL=dxworks.service.d.ts.map