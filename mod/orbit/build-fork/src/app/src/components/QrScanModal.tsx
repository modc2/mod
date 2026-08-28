"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { decodeQr } from "../app/lib/qrscan";

interface QrScanModalProps {
  title?: string;
  /** One line under the title explaining what to point the camera at. */
  hint?: string;
  /**
   * Maps a scanned payload to the value to hand back — return null to reject
   * it and keep scanning (e.g. a QR that holds no address).
   */
  accept?: (text: string) => string | null;
  /** Shown when `accept` rejects a code, so a bad scan isn't silent. */
  rejectHint?: string;
  onResult: (value: string) => void;
  onClose: () => void;
}

type Status = "starting" | "scanning" | "nocam" | "reading";

const ACCENT = "var(--crt-green, #4ade80)";

// Decoding every frame is wasted work — a camera at 30fps gives us ten
// chances a second at a code that isn't moving.
const SCAN_INTERVAL_MS = 120;
// Frames are downscaled before decoding; beyond this the extra pixels only
// cost time (a QR needs ~3px per module, not 30).
const MAX_DECODE_PX = 720;

/**
 * Camera QR reader — points at someone's address QR and hands back the text.
 *
 * Decoding happens locally (Chromium's BarcodeDetector where it exists,
 * lib/qrscan.ts everywhere else); frames are never uploaded. There is always
 * a file path too: browsers without a camera, without permission, or on a
 * plain-http origin (where getUserMedia is blocked outright) can still drop
 * in a screenshot.
 */
export default function QrScanModal({
  title = "Scan a QR code",
  hint,
  accept,
  rejectHint = "That code doesn't contain what we're looking for.",
  onResult,
  onClose,
}: QrScanModalProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const detectorRef = useRef<any>(null);
  const doneRef = useRef(false);

  const [status, setStatus] = useState<Status>("starting");
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [cameras, setCameras] = useState<MediaDeviceInfo[]>([]);
  const [camIdx, setCamIdx] = useState(0);

  // A hit fires once — the modal is closing, later frames are noise.
  const handleText = useCallback(
    (text: string): boolean => {
      if (doneRef.current) return true;
      const value = accept ? accept(text) : text;
      if (!value) {
        setNote(rejectHint);
        return false;
      }
      doneRef.current = true;
      try {
        navigator.vibrate?.(40);
      } catch {
        /* not everywhere, never important */
      }
      onResult(value);
      onClose();
      return true;
    },
    [accept, onClose, onResult, rejectHint]
  );

  /** Draw the current source onto the scratch canvas and decode it. */
  const decodeSource = useCallback(
    async (source: CanvasImageSource, sw: number, sh: number): Promise<string | null> => {
      const canvas = canvasRef.current;
      if (!canvas || !sw || !sh) return null;
      const scale = Math.min(1, MAX_DECODE_PX / Math.max(sw, sh));
      canvas.width = Math.round(sw * scale);
      canvas.height = Math.round(sh * scale);
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) return null;
      ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
      if (detectorRef.current) {
        try {
          const hits = await detectorRef.current.detect(canvas);
          if (hits?.length && hits[0].rawValue) return hits[0].rawValue as string;
        } catch {
          detectorRef.current = null; // fall through to ours, for good
        }
      }
      const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
      return decodeQr(img.data, canvas.width, canvas.height);
    },
    []
  );

  // Camera lifecycle: one stream per selected device, torn down on close.
  useEffect(() => {
    let cancelled = false;
    const stop = () => {
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    };

    (async () => {
      if (!navigator.mediaDevices?.getUserMedia) {
        // Also the plain-http case: getUserMedia only exists on secure origins.
        setStatus("nocam");
        setError(
          window.isSecureContext === false
            ? "The camera needs a secure (https) connection — use an image instead."
            : "No camera available on this device — use an image instead."
        );
        return;
      }
      try {
        const devices = await navigator.mediaDevices.enumerateDevices().catch(() => []);
        const vids = devices.filter((d) => d.kind === "videoinput");
        const stream = await navigator.mediaDevices.getUserMedia({
          video: vids[camIdx]?.deviceId
            ? { deviceId: { exact: vids[camIdx].deviceId } }
            : { facingMode: { ideal: "environment" } },
          audio: false,
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        stop();
        streamRef.current = stream;
        // Labels only populate once permission is granted — re-list for the
        // camera switcher.
        setCameras((await navigator.mediaDevices.enumerateDevices().catch(() => [])).filter((d) => d.kind === "videoinput"));
        const v = videoRef.current;
        if (v) {
          v.srcObject = stream;
          await v.play().catch(() => {});
        }
        setStatus("scanning");
        setError(null);
      } catch (e) {
        if (cancelled) return;
        const name = (e as DOMException)?.name;
        setStatus("nocam");
        setError(
          name === "NotAllowedError"
            ? "Camera permission denied — allow it in your browser, or use an image."
            : name === "NotFoundError"
            ? "No camera found — use an image instead."
            : `Camera unavailable (${name || "error"}) — use an image instead.`
        );
      }
    })();

    return () => {
      cancelled = true;
      stop();
    };
  }, [camIdx]);

  // Chromium ships a native detector; prefer it, fall back to ours.
  useEffect(() => {
    const Detector = (window as any).BarcodeDetector;
    if (!Detector) return;
    (async () => {
      try {
        const formats = await Detector.getSupportedFormats?.();
        if (formats && !formats.includes("qr_code")) return;
        detectorRef.current = new Detector({ formats: ["qr_code"] });
      } catch {
        detectorRef.current = null;
      }
    })();
  }, []);

  // The scan loop.
  useEffect(() => {
    if (status !== "scanning") return;
    let busy = false;
    const id = window.setInterval(async () => {
      const v = videoRef.current;
      if (busy || doneRef.current || !v || v.readyState < 2) return;
      busy = true;
      try {
        const text = await decodeSource(v, v.videoWidth, v.videoHeight);
        if (text) handleText(text);
      } catch {
        /* a dropped frame is not an error worth showing */
      } finally {
        busy = false;
      }
    }, SCAN_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [status, decodeSource, handleText]);

  // Esc closes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  /** Decode a still image — an upload, or a screenshot pasted from the clipboard. */
  const readImage = useCallback(
    async (file: Blob) => {
      setNote(null);
      setStatus((s) => (s === "scanning" ? s : "reading"));
      try {
        const bmp = await createImageBitmap(file);
        const text = await decodeSource(bmp, bmp.width, bmp.height);
        bmp.close?.();
        if (!text) setNote("No QR code found in that image.");
        else handleText(text);
      } catch {
        setNote("Couldn't read that image.");
      } finally {
        setStatus((s) => (s === "reading" ? "nocam" : s));
      }
    },
    [decodeSource, handleText]
  );

  // Paste a screenshot straight in — the desktop path when there's no camera.
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const item = Array.from(e.clipboardData?.items || []).find((i) => i.type.startsWith("image/"));
      const file = item?.getAsFile();
      if (file) {
        e.preventDefault();
        readImage(file);
      }
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [readImage]);

  const live = status === "scanning";

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-[320] flex items-center justify-center p-4"
      style={{ background: "rgba(7, 7, 13, 0.68)", backdropFilter: "blur(8px)", WebkitBackdropFilter: "blur(8px)" }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="rounded-2xl w-full max-w-sm flex flex-col overflow-hidden"
        style={{
          background: "color-mix(in srgb, var(--glass-bg, var(--bg-primary)) 92%, transparent)",
          border: "1px solid var(--border-color-strong)",
          boxShadow: "0 18px 60px rgba(0,0,0,0.55)",
          backdropFilter: "blur(18px) saturate(150%)",
          WebkitBackdropFilter: "blur(18px) saturate(150%)",
        }}
      >
        <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: "1px solid var(--border-color)" }}>
          <span className="text-[12px] uppercase font-bold tracking-[0.14em] truncate" style={{ color: "var(--text-primary)" }}>
            {title}
          </span>
          <button onClick={onClose} className="text-[13px] px-1.5 transition-colors" style={{ color: "var(--text-tertiary)" }} title="Close (Esc)">
            ✕
          </button>
        </div>

        <div className="flex flex-col gap-3 p-4">
          {hint && (
            <span className="text-[11px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
              {hint}
            </span>
          )}

          {/* Viewfinder */}
          <div
            className="relative w-full overflow-hidden rounded-xl"
            style={{ aspectRatio: "1 / 1", background: "var(--bg-secondary)", border: "1px solid var(--border-color)" }}
          >
            <video
              ref={videoRef}
              playsInline
              muted
              autoPlay
              className="absolute inset-0 w-full h-full object-cover"
              style={{ opacity: live ? 1 : 0, transition: "opacity 200ms" }}
            />
            {/* Corner reticle */}
            {live && (
              <div className="absolute inset-0 pointer-events-none" style={{ padding: "14%" }}>
                <div className="w-full h-full relative">
                  {[
                    { top: 0, left: 0, borderTop: 2, borderLeft: 2 },
                    { top: 0, right: 0, borderTop: 2, borderRight: 2 },
                    { bottom: 0, left: 0, borderBottom: 2, borderLeft: 2 },
                    { bottom: 0, right: 0, borderBottom: 2, borderRight: 2 },
                  ].map((c, i) => (
                    <span
                      key={i}
                      className="absolute"
                      style={{
                        width: 26,
                        height: 26,
                        top: c.top as number | undefined,
                        left: c.left as number | undefined,
                        right: c.right as number | undefined,
                        bottom: c.bottom as number | undefined,
                        borderTop: c.borderTop ? `2px solid ${ACCENT}` : undefined,
                        borderLeft: c.borderLeft ? `2px solid ${ACCENT}` : undefined,
                        borderRight: c.borderRight ? `2px solid ${ACCENT}` : undefined,
                        borderBottom: c.borderBottom ? `2px solid ${ACCENT}` : undefined,
                        boxShadow: `0 0 8px color-mix(in srgb, ${ACCENT} 45%, transparent)`,
                      }}
                    />
                  ))}
                </div>
              </div>
            )}
            {!live && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-5 text-center">
                <span className="text-[22px]" style={{ color: "var(--text-tertiary)" }}>
                  {status === "starting" || status === "reading" ? "◌" : "▣"}
                </span>
                <span className="text-[11px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
                  {status === "starting"
                    ? "Starting the camera…"
                    : status === "reading"
                    ? "Reading image…"
                    : error || "Camera unavailable."}
                </span>
              </div>
            )}
          </div>

          {note && (
            <div className="text-[10.5px] leading-relaxed" style={{ color: "var(--crt-amber, #fbbf24)" }}>
              {note}
            </div>
          )}

          <div className="flex items-center gap-1.5">
            <label
              className="flex-1 text-[10px] px-3 py-1.5 rounded uppercase font-bold tracking-wider text-center cursor-pointer transition-all"
              style={{
                color: "var(--text-secondary)",
                background: "var(--bg-secondary)",
                border: "1px solid var(--border-color)",
              }}
              title="Decode a QR out of a screenshot or photo"
            >
              Use an image
              <input
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  e.target.value = "";
                  if (f) readImage(f);
                }}
              />
            </label>
            {cameras.length > 1 && (
              <button
                onClick={() => setCamIdx((i) => (i + 1) % cameras.length)}
                className="text-[10px] px-3 py-1.5 rounded uppercase font-bold tracking-wider transition-all shrink-0"
                style={{
                  color: "var(--text-secondary)",
                  background: "var(--bg-secondary)",
                  border: "1px solid var(--border-color)",
                }}
                title={`Switch camera (${camIdx + 1}/${cameras.length})`}
              >
                ⟳ Camera
              </button>
            )}
          </div>

          <span className="text-[9px] uppercase tracking-[0.1em]" style={{ color: "var(--text-tertiary)" }}>
            {live ? "Scanning · nothing leaves this device" : "You can also paste a screenshot"}
          </span>
        </div>
      </div>
      <canvas ref={canvasRef} className="hidden" />
    </div>
  );
}
