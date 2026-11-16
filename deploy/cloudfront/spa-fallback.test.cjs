// spa-fallback.js 로컬 확인 (node만 있으면 된다, 의존성 없음):  node deploy/cloudfront/spa-fallback.test.cjs
// CloudFront에는 spa-fallback.js만 올린다. 이 파일은 CloudFront Functions의 이벤트 모양(event.request의 uri·querystring)을
// 흉내 내어 함수를 strict mode로 돌려 보고, 경로마다 바뀐 uri와 쿼리 문자열이 남는지 확인한다 (실패하면 종료 코드 1).
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE_PATH = path.join(__dirname, 'spa-fallback.js');
const source = fs.readFileSync(SOURCE_PATH, 'utf8');

// CloudFront Functions 제약 가운데 이 파일로 확인할 수 있는 것: 함수 크기 10 KB, 모듈 문법·외부 모듈 없음.
const MAX_FUNCTION_BYTES = 10 * 1024;
assert.ok(Buffer.byteLength(source, 'utf8') <= MAX_FUNCTION_BYTES, 'function larger than 10 KB');
assert.ok(!/\bmodule\.exports\b|\brequire\s*\(|^\s*export\s/m.test(source), 'function must not use module syntax');

// CloudFront Functions처럼 strict mode로, 다른 전역 없이 handler만 꺼낸다.
const context = vm.createContext({});
vm.runInContext('"use strict";\n' + source + '\n;globalThis.__handler = handler;', context, { filename: SOURCE_PATH });
const handler = context.__handler;
assert.equal(typeof handler, 'function', 'handler is not defined');

// '/auth/github/callback?code=x&state=y' → CloudFront 뷰어 요청 이벤트 (쿼리는 uri가 아니라 querystring 객체에 들어 있다).
function viewerRequest(target) {
  const [uri, query = ''] = target.split('?');
  const querystring = {};
  for (const pair of query ? query.split('&') : []) {
    const [key, value = ''] = pair.split('=');
    if (querystring[key]) {
      const entry = querystring[key];
      entry.multiValue = entry.multiValue || [{ value: entry.value }];
      entry.multiValue.push({ value });
    } else {
      querystring[key] = { value };
    }
  }
  return {
    version: '1.0',
    context: { eventType: 'viewer-request' },
    viewer: { ip: '198.51.100.11' },
    request: {
      method: 'GET',
      uri,
      querystring,
      headers: { host: { value: 'd111111abcdef8.cloudfront.net' } },
      cookies: {},
    },
  };
}

const INDEX = '/index.html';
// [요청 경로, 기대 uri]. 앞의 10개는 배포 문서에 적은 필수 경우, 나머지는 FE 라우트와 경계 경우.
const CASES = [
  ['/', INDEX],
  ['/index.html', '/index.html'],
  ['/dashboard', INDEX],
  ['/auth/github/callback?code=x&state=y', INDEX],
  ['/share/abc', INDEX],
  ['/assets/index-xyz.js', '/assets/index-xyz.js'],
  ['/favicon.svg', '/favicon.svg'],
  ['/api/v1/users/me', '/api/v1/users/me'],
  ['/api/v1/public/shares/x', '/api/v1/public/shares/x'],
  ['/some.path/with.dot/route', INDEX],
  ['/login', INDEX],
  ['/register', INDEX],
  ['/issue', INDEX],
  ['/portfolio', INDEX],
  ['/career', INDEX],
  ['/auth/google/callback?code=4%2F0Ab&state=s1&scope=openid%20email', INDEX],
  ['/auth/naver/callback?code=c&state=s', INDEX],
  ['/share/Zm9vYmFyX2Jhei1xdXV4LTEyMzQ1Njc4OTAxMjM0NTY3', INDEX],
  ['/dashboard/', INDEX],
  ['/assets/index-DNXHIdFX.js', '/assets/index-DNXHIdFX.js'],
  ['/assets/index-Bi9Rffqp.css', '/assets/index-Bi9Rffqp.css'],
  ['/assets/deleted-old-chunk.js', '/assets/deleted-old-chunk.js'],
  ['/api/v1/users/login', '/api/v1/users/login'],
  ['/dashboard?tab=1&tab=2', INDEX],
];

let failures = 0;
const rows = [];
for (const [target, expected] of CASES) {
  const event = viewerRequest(target);
  const before = JSON.parse(JSON.stringify(event.request));
  let result;
  let problem = '';
  try {
    result = handler(event);
    assert.equal(result, event.request, 'must return the same request object');
    assert.equal(result.uri, expected, 'uri');
    assert.equal(result.method, before.method, 'method changed');
    assert.deepEqual(result.querystring, before.querystring, 'query string changed');
    assert.deepEqual(result.headers, before.headers, 'headers changed');
    assert.deepEqual(result.cookies, before.cookies, 'cookies changed');
  } catch (error) {
    failures += 1;
    problem = error.message;
  }
  const query = Object.keys(before.querystring).length ? JSON.stringify(result ? result.querystring : null) : '-';
  rows.push(`${problem ? 'FAIL' : 'ok  '}  ${target.padEnd(52)} -> ${String(result && result.uri).padEnd(28)} query ${query}${problem ? '  (' + problem + ')' : ''}`);
}

console.log(rows.join('\n'));
console.log(`\n${CASES.length - failures}/${CASES.length} passed (function ${Buffer.byteLength(source, 'utf8')} bytes)`);
process.exitCode = failures ? 1 : 0;
