// SPA 되돌리기(history fallback) CloudFront Function. 런타임 cloudfront-js-2.0, 이벤트 뷰어 요청(viewer request).
// 배포판의 기본(S3) 동작에만 연결한다. /api/* 동작에는 연결하지 않는다 (아래 첫 검사는 잘못 연결했을 때의 안전장치).
//
// - /api/로 시작하는 경로: 그대로 둔다 (BE의 JSON 응답을 index.html로 바꾸지 않는다).
// - 마지막 경로 조각에 점(.)이 있는 경로(/assets/index-xyz.js, /favicon.svg, /index.html): 그대로 둔다. 없는 파일은
//   S3(OAC)가 403을 돌려준다 (HTML로 바꾸지 않아야 지운 해시 자산을 HTML로 받는 빈 화면이 생기지 않는다).
// - 그 밖(/, /dashboard, /auth/github/callback, /share/<token> 등 FE 라우터 경로): uri만 /index.html로 바꾼다.
//   쿼리 문자열은 request.querystring에 따로 있어 그대로 남는다 (OAuth 콜백의 code·state). 공유 링크 토큰은
//   secrets.token_urlsafe 값이라 점이 없다.
//
// AWS 문서의 SPA 예시('Add index.html to request URLs without a file name')는 /dashboard를 /dashboard/index.html로 바꿔
// 이 앱에서는 403이 된다. 배포판 전체 오류 페이지(403/404 → /index.html)는 /api/*의 JSON 오류까지 바꾸므로 쓰지 않는다.
// 이 파일을 고치면 같은 폴더의 spa-fallback.test.cjs를 node로 실행해 확인한다 (로컬 전용, CloudFront에 올리지 않음).
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
