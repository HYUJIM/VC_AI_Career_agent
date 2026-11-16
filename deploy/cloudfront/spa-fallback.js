// SPA history fallback. CloudFront Functions runtime cloudfront-js-2.0, viewer request event.
// Attach it to the default (S3) behavior only, never to /api/* (the first check is a safety net).
// - /api/... : unchanged (keep BE JSON responses).
// - last path segment has a dot (/assets/x.js, /favicon.svg, /index.html): unchanged (missing file -> S3 403).
// - anything else (/, /dashboard, /auth/<provider>/callback, /share/<token>): uri -> /index.html.
//   The query string lives in request.querystring and is kept (OAuth code and state).
// Keep this file ASCII-only. Notes and the local test: spa-fallback.test.cjs (not uploaded).
function handler(event) {
    var request = event.request;
    var uri = request.uri;
    if (uri.indexOf('/api/') === 0) {
        return request;
    }
    var last = uri.substring(uri.lastIndexOf('/') + 1);
    if (last.indexOf('.') !== -1) {
        return request;
    }
    request.uri = '/index.html';
    return request;
}
