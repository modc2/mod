// onnxruntime-web, loaded from the CDN at run time — the tab needs network
// access to it either way (the wasm binaries come from the same place), so
// bundling the JS half would not remove the dependency.
const CDN = 'https://cdn.jsdelivr.net/npm/onnxruntime-web/dist/';

let ORT: any = null;

export async function ort(): Promise<any> {
  if (ORT) return ORT;
  await new Promise<void>((ok, no) => {
    const s = document.createElement('script');
    s.src = CDN + 'ort.min.js';
    s.onload = () => ok();
    s.onerror = () =>
      no(new Error('could not load onnxruntime-web from the CDN — '
        + 'this tab needs network access to ' + CDN));
    document.head.appendChild(s);
  });
  ORT = (window as any).ort;
  ORT.env.wasm.wasmPaths = CDN;
  return ORT;
}

// The server feeds the model seeded gaussians for floats and 0/1 for integer
// inputs (which are almost always embedding indices). Same rule here, so the
// two benchmarks are doing the same amount of work.
export function makeTensor(o: any, spec: any, seed: number) {
  let s = seed >>> 0;
  const rnd = () => (s = (s * 1664525 + 1013904223) >>> 0) / 4294967296;
  const gauss = () => {
    const u = Math.max(rnd(), 1e-9);
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rnd());
  };
  const n = spec.shape.reduce((a: number, b: number) => a * b, 1);
  const d = spec.dtype;
  const fill = (a: any, f: () => any) => { for (let i = 0; i < n; i++) a[i] = f(); return a; };
  if (d === 'float32') return new o.Tensor('float32', fill(new Float32Array(n), gauss), spec.shape);
  if (d === 'float64') return new o.Tensor('float64', fill(new Float64Array(n), gauss), spec.shape);
  // 0 and 1 only: an integer input is nearly always an index into an embedding
  // table, and a random int64 is an out-of-range lookup and a crash.
  const bit = () => (rnd() < 0.5 ? 0 : 1);
  if (d === 'int64') return new o.Tensor('int64', fill(new BigInt64Array(n), () => BigInt(bit())), spec.shape);
  if (d === 'int32') return new o.Tensor('int32', fill(new Int32Array(n), bit), spec.shape);
  if (d === 'bool') return new o.Tensor('bool', fill(new Uint8Array(n), bit), spec.shape);
  if (d === 'uint8') return new o.Tensor('uint8', fill(new Uint8Array(n), bit), spec.shape);
  if (d === 'int8') return new o.Tensor('int8', fill(new Int8Array(n), bit), spec.shape);
  throw new Error(`this console does not generate ${d} inputs — bench it on the `
    + `server instead, or feed the model real inputs`);
}
