// Study 03: re-verify retained signed credentials at run time (no stored status is read).
// Usage: tsx scripts/verify-study03.ts <fixtureDir> <input.json> <output.json>
// input: [{ externalId, compactJws }]; fixtureDir holds dids/ and registry/ snapshots.
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import path from 'node:path';

async function main() {
  const [fixtureDir, inputPath, outputPath] = process.argv.slice(2);
  if (!fixtureDir || !inputPath || !outputPath) throw new Error('usage: <fixtureDir> <input.json> <output.json>');
  if (existsSync(outputPath)) throw new Error('output exists; refusing to overwrite');
  const root = path.resolve(fixtureDir);
  // Must precede the first import of config/resolver.
  process.env.FIXTURES_DIR = root;
  const { verifyAndNormalize } = await import('../src/verify/pipeline.js');
  const { env } = await import('../src/config/env.js');
  if (path.resolve(env.fixturesDir) !== root) throw new Error('Resolver already initialized for another fixture root');
  const { clearDidDocumentCache } = await import('../src/resolver/didDocumentStore.js');
  clearDidDocumentCache();
  const trust = JSON.parse(readFileSync(path.join(root, 'registry/trust-registry.json'), 'utf8')) as Record<string, string>;
  const revoked = new Set<string>((JSON.parse(readFileSync(path.join(root, 'registry/revocation-list.json'), 'utf8'))
    .revoked_external_ids) as string[]);
  const items = JSON.parse(readFileSync(inputPath, 'utf8')) as { externalId: string; compactJws: string }[];
  const out: Record<string, unknown> = {};
  for (const item of items) {
    const record = await verifyAndNormalize({ externalId: item.externalId, provider: 'upload', compactJws: item.compactJws }, 'upload', {
      issuerTrust: { getTrustLevel: async (did: string) => (trust[did] ?? 'unknown') as never },
      revocationChecker: { isRevoked: async (id: string) => revoked.has(id) },
      anchorVerifier: { checkAnchor: async () => ({ checked: false, match: null, detail: 'Research fixture: no ledger anchor' }) },
    });
    out[item.externalId] = {
      status: record.verification.status,
      failed_checks: record.verification.checks.filter((c) => c.result === 'fail').map((c) => c.check),
      record,
    };
  }
  writeFileSync(outputPath, JSON.stringify({
    version: 'study03-live-verification/1.0', verified_at: new Date().toISOString(), node: process.version,
    cryptographic_verification_performed: true, fixture_dir: root, count: items.length, results: out,
  }, null, 1));
  console.log(JSON.stringify({ verified: items.length }));
}

main().catch((e) => { console.error(e); process.exit(1); });
