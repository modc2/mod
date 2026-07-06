// Webpack `?raw` query — files imported with this suffix resolve to a
// UTF-8 string at build time (rule lives in next.config.mjs). Declare here
// so TS accepts e.g. `import src from "./foo?raw"`.
declare module "*?raw" {
  const content: string;
  export default content;
}
