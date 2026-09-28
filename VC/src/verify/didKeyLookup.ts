import type { KeyObject } from "node:crypto";
import type { DIDDocument, VerificationMethod } from "did-resolver";
import { publicKeyFromJwk, type Ed25519JwkPublic } from "../crypto/keys.js";

export function findVerificationMethod(didDocument: DIDDocument, verificationMethodId: string): VerificationMethod | undefined {
  return didDocument.verificationMethod?.find((vm) => vm.id === verificationMethodId);
}

export function publicKeyFromVerificationMethod(vm: VerificationMethod): KeyObject {
  const jwk = vm.publicKeyJwk as unknown as Ed25519JwkPublic | undefined;
  if (!jwk) {
    throw new Error(`verificationMethod(${vm.id})에 publicKeyJwk가 없습니다`);
  }
  return publicKeyFromJwk(jwk);
}
