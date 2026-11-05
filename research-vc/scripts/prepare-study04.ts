/** Study 04: same signing procedure as prepare-paper-study.ts, any family count. Does not touch existing fixtures or keys. */
import { createHash, generateKeyPairSync, randomUUID } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { signCompactJws } from '../src/crypto/jws.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const hash = (b: string | Buffer) => createHash('sha256').update(b).digest('hex');
const write = (p: string, value: unknown) => {
  mkdirSync(path.dirname(p), { recursive: true });
  writeFileSync(p, JSON.stringify(value, null, 2) + '\n', { encoding: 'utf8', flag: 'wx' });
};

export async function prepare(worldPath: string, outputPath: string) {
  const output = path.resolve(outputPath);
  const original = path.join(root, 'fixtures');
  if (output === original || output.startsWith(original + path.sep) || existsSync(output)) {
    throw new Error('Output must be a new directory outside original fixtures');
  }
  const worldBytes = readFileSync(worldPath);
  const world = JSON.parse(worldBytes.toString('utf8'));
  if (world.version !== 'paper-world/2.1' || world.study !== 'study04') throw new Error('Expected study04 world');
  mkdirSync(output, { recursive: true });
  writeFileSync(path.join(output, 'world.input.json'), worldBytes, { flag: 'wx' });
  const issuers = ['east', 'west', 'north'].map(slug => {
    const keys = generateKeyPairSync('ed25519');
    const did = `did:fixture:study-${slug}`;
    return { did, kid: `${did}#key-1`, name: `교육기록센터 ${slug.toUpperCase()}`, keys };
  });
  const dids = Object.fromEntries(issuers.map(i => [i.did, {
    '@context': ['https://www.w3.org/ns/did/v1'], id: i.did,
    verificationMethod: [{ id: i.kid, type: 'JsonWebKey2020', controller: i.did,
      publicKeyJwk: i.keys.publicKey.export({ format: 'jwk' }) }], assertionMethod: [i.kid]
  }]));
  write(path.join(output, 'dids/did-documents.json'), dids);
  // Normalization consults this public mapping even when alignments are empty.
  write(path.join(output, 'registry/skill-alias-dict.json'),
    JSON.parse(readFileSync(path.join(original, 'registry/skill-alias-dict.json'), 'utf8')));
  // Must precede the first import of config/resolver; never change original data.
  process.env.FIXTURES_DIR = output;
  const { verifyAndNormalize } = await import('../src/verify/pipeline.js');
  const { env } = await import('../src/config/env.js');
  if (path.resolve(env.fixturesDir) !== output) throw new Error('Resolver already initialized for another fixture root');
  const { clearDidDocumentCache } = await import('../src/resolver/didDocumentStore.js');
  clearDidDocumentCache();
  const types = ['none', 'forged_signature', 'revoked', 'expired', 'untrusted_issuer', 'valid_conflict'];
  const invalid = types.slice(1, 5);
  const revoked = new Set<string>();
  const exported: unknown[] = [];
  const cases: unknown[] = [];
  const started = new Date().toISOString();
  for (const family of world.families) {
    for (const kind of types) {
      const caseId = `${family.family_id}-${kind}`;
      const truth = invalid.includes(kind) ? (family.index + invalid.indexOf(kind)) % 2 === 0 : null;
      const caseIds: string[] = [];
      for (let slot = 0; slot < 4; slot++) {
        const target = slot >= 2;
        const issuer = issuers[target && kind === 'untrusted_issuer' ? 2 : slot % 2]!;
        const id = randomUUID(); // No type/condition/subject labels enter IDs.
        const factIndex = kind === 'valid_conflict' && target ? 2 : slot;
        const fact = family.facts[factIndex];
        const falseContent = (target && truth === false) || (kind === 'valid_conflict' && slot === 3);
        const statement = falseContent ? family.counterfacts[factIndex] : fact.value;
        const document = {
          id: `https://research.example/assertions/${id}`, type: 'Assertion',
          recipient: { identity: family.subject_did, type: 'did' },
          badge: {
            id: `https://research.example/badges/${id}`, name: family.record_names[factIndex],
            description: statement, criteria: { narrative: '기록된 활동 내용에 대한 발급 기록.' },
            issuer: { id: issuer.did, name: issuer.name, url: 'https://research.example/issuer' }, alignment: []
          },
          issuedOn: '2025-06-01T00:00:00Z',
          expires: target && kind === 'expired' ? '2025-12-01T00:00:00Z' : '2050-01-01T00:00:00Z',
          evidence: []
        };
        // For false forged payloads, sign the truthful document then alter payload.
        let jws = signCompactJws(target && kind === 'forged_signature' && falseContent
          ? { ...document, badge: { ...document.badge, description: fact.value } } : document,
          issuer.keys.privateKey, issuer.kid);
        if (target && kind === 'forged_signature') {
          const parts = jws.split('.');
          if (falseContent) parts[1] = Buffer.from(JSON.stringify(document)).toString('base64url');
          else {
            const bytes = Buffer.from(parts[2]!, 'base64url'); bytes[0] = bytes[0]! ^ 1;
            parts[2] = bytes.toString('base64url');
          }
          jws = parts.join('.');
        }
        if (target && kind === 'revoked') revoked.add(id);
        const ref = { externalId: id, provider: 'upload' as const, compactJws: jws };
        const record = await verifyAndNormalize(ref, 'upload', {
          issuerTrust: { getTrustLevel: async did => did === issuers[2]!.did ? 'unknown' : 'known' },
          revocationChecker: { isRevoked: async external => revoked.has(external) },
          anchorVerifier: { checkAnchor: async () => ({ checked: false, match: null, detail: 'Research fixture: no ledger anchor' }) }
        });
        const failed = record.verification.checks.filter(c => c.result === 'fail').map(c => c.check);
        const expected = target ? ({forged_signature:'signature',revoked:'revocation',expired:'expiration',untrusted_issuer:'issuer_trust'} as Record<string,string>)[kind] : undefined;
        if (JSON.stringify(failed) !== JSON.stringify(expected ? [expected] : [])) {
          write(path.join(output, 'quarantine.json'), { caseId, ref, record, expected });
          throw new Error(`Unexpected verification checks for ${caseId}/${slot}`);
        }
        caseIds.push(record.record_id);
        exported.push({ case_id: caseId, slot, fact_index: factIndex, false_content: falseContent,
          raw_ref: ref, jws_sha256: hash(jws), record });
      }
      cases.push({ case_id: caseId, family_id: family.family_id, kind, target_truth: truth, record_ids: caseIds });
    }
  }
  const trust = Object.fromEntries(issuers.map((i,n) => [i.did, n === 2 ? 'unknown' : 'known']));
  write(path.join(output, 'registry/trust-registry.json'), trust);
  write(path.join(output, 'registry/revocation-list.json'), { revoked_external_ids: [...revoked] });
  const codeFiles = ['scripts/prepare-study04.ts','src/verify/pipeline.ts','src/verify/checks.ts',
    'src/crypto/jws.ts','src/crypto/canonical.ts','src/crypto/keys.ts','src/parsers/ob2Parser.ts',
    'src/parsers/parseCredential.ts','src/normalize/normalizeCredential.ts','src/normalize/skillMapper.ts',
    'src/resolver/didDocumentStore.ts','src/resolver/fixtureDidDriver.ts','src/config/env.ts'];
  const snapshots = ['dids/did-documents.json','registry/trust-registry.json','registry/revocation-list.json','registry/skill-alias-dict.json'];
  write(path.join(output, 'verification_bundle.json'), {
    version: 'paper-verification/2.1', world_sha256: hash(worldBytes),
    cryptographic_verification_performed: true, suite: 'Compact-JWS-EdDSA-fixture',
    started_at: started, finished_at: new Date().toISOString(), node: process.version,
    code_sha256: Object.fromEntries(codeFiles.map(p => [p, hash(readFileSync(path.join(root,p)))])),
    public_snapshots: Object.fromEntries(snapshots.map(p => [p, { sha256: hash(readFileSync(path.join(output,p))),
      text: readFileSync(path.join(output,p),'utf8') }])),
    cases, records: exported, limitations: ['Fixture DID method and local trust/revocation; no operational trust claim.',
      'Ephemeral private test keys were not persisted; public keys and signed bytes are retained.']
  });
  return { cases: cases.length, records: exported.length, output };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [world, output] = process.argv.slice(2);
  if (!world || !output) throw new Error('Usage: prepare-study04.ts WORLD.json NEW_DIRECTORY');
  prepare(world, output).then(r => console.log(JSON.stringify(r))).catch(e => { console.error(e.message); process.exitCode = 1; });
}
