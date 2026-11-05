import { generateKeyPairSync, createPublicKey, createPrivateKey, type KeyObject } from "node:crypto";

export interface Ed25519JwkPublic {
  kty: "OKP";
  crv: "Ed25519";
  x: string;
}

export interface Ed25519JwkPrivate extends Ed25519JwkPublic {
  d: string;
}

export interface FixtureKeyPair {
  /** DID Document에 그대로 넣을 수 있는 공개키 JWK */
  publicJwk: Ed25519JwkPublic;
  /** 서명 생성 시에만 사용하는 비밀키 JWK. 실제 서비스에서는 절대 커밋하지 않는다. */
  privateJwk: Ed25519JwkPrivate;
}

/**
 * 테스트/fixture 전용 Ed25519 키쌍을 생성한다.
 * 주의: 이 함수로 만든 키는 오직 로컬 목업 데이터 서명용이며 실제 운영 키가 아니다.
 */
export function generateFixtureKeyPair(): FixtureKeyPair {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const publicJwk = publicKey.export({ format: "jwk" }) as Ed25519JwkPublic;
  const privateJwk = privateKey.export({ format: "jwk" }) as Ed25519JwkPrivate;
  return { publicJwk, privateJwk };
}

export function publicKeyFromJwk(jwk: Ed25519JwkPublic): KeyObject {
  return createPublicKey({ key: { ...jwk, kty: "OKP", crv: "Ed25519" }, format: "jwk" });
}

export function privateKeyFromJwk(jwk: Ed25519JwkPrivate): KeyObject {
  return createPrivateKey({ key: { ...jwk, kty: "OKP", crv: "Ed25519" }, format: "jwk" });
}
