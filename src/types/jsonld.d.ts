/**
 * jsonld 패키지는 공식 TypeScript 타입을 제공하지 않는다.
 * 이 프로젝트에서 실제로 쓰는 canonize() 시그니처만 최소한으로 선언해서 사용한다.
 */
declare module "jsonld" {
  interface JsonLdDocumentLoaderResult {
    contextUrl: string | null;
    document: unknown;
    documentUrl: string;
  }

  interface CanonizeOptions {
    algorithm?: "URDNA2015" | "URGNA2012";
    format?: "application/n-quads";
    documentLoader?: (url: string) => Promise<JsonLdDocumentLoaderResult>;
    safe?: boolean;
  }

  interface JsonLdApi {
    canonize(input: unknown, options?: CanonizeOptions): Promise<string>;
  }

  const jsonld: JsonLdApi;
  export default jsonld;
}
