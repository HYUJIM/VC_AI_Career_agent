import { beforeAll, describe, expect, it } from 'vitest';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { prepare } from '../scripts/prepare-paper-study.js';

describe('isolated paper-study signature evidence', () => {
  let evidence: any;
  let originalHash: string;
  let originalText: string;
  beforeAll(async () => {
    const original = path.resolve('fixtures/dids/did-documents.json');
    originalText=readFileSync(original,'utf8');
    const folder=mkdtempSync(path.join(tmpdir(),'vc-paper-contract-'));
    const world=path.join(folder,'world.json');
    writeFileSync(world,JSON.stringify({version:'paper-world/2.1',families:Array.from({length:10},(_,i)=>({
      family_id:`synthetic-${i}`,index:i,subject_did:`did:fixture:unit-person-${i}`,
      facts:Array.from({length:4},(_,j)=>({value:`Synthetic activity ${i} part ${j}`})),
      counterfacts:['counter zero','counter one','counter two','counter three'],
      record_names:['activity A','activity B','activity C','activity D']
    }))}));
    const output=path.join(folder,'signed');await prepare(world,output);
    evidence=JSON.parse(readFileSync(path.join(output,'verification_bundle.json'),'utf8'));
    expect(readFileSync(original,'utf8')).toBe(originalText);
  });
  it('verifies real signatures and only the intended failing check',()=>{
    expect(evidence.records).toHaveLength(240);
    for (const item of evidence.records) {
      const kind=evidence.cases.find((c:any)=>c.case_id===item.case_id).kind;
      const failures=item.record.verification.checks.filter((c:any)=>c.result==='fail').map((c:any)=>c.check);
      const failure=({forged_signature:'signature',revoked:'revocation',expired:'expiration',untrusted_issuer:'issuer_trust'} as any)[kind];
      expect(failures).toEqual(item.slot>=2 && failure ? [failure] : []);
    }
  });
  it('separates truthful invalid and false valid-conflict records',()=>{
    const invalidTrue=evidence.records.filter((r:any)=>r.slot>=2 && r.case_id.endsWith('forged_signature') && !r.false_content);
    expect(invalidTrue.length).toBeGreaterThan(0);
    const validFalse=evidence.records.filter((r:any)=>r.case_id.endsWith('valid_conflict') && r.false_content);
    expect(validFalse).toHaveLength(10);
    expect(validFalse.every((r:any)=>r.record.verification.status==='verified')).toBe(true);
  });
  it('stores public provenance without private key material',()=>{
    expect(evidence.cryptographic_verification_performed).toBe(true);
    expect(evidence.public_snapshots['dids/did-documents.json']).toBeDefined();
    expect(JSON.stringify(evidence)).not.toContain('privateJwk');
    expect(evidence.records.every((r:any)=>r.record.verification.checks.some((c:any)=>c.check==='anchor_match' && c.result==='skip'))).toBe(true);
  });
});
