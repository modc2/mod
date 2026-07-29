#!/usr/bin/env python3
"""Layout efficiency matrix for the jcode iOS app.

A single screenshot only measures one content state. Real UI quality means the
layout stays efficient across the *range* of content (empty, short, one tool,
long thread, code-heavy) and devices. This harness:

  1. builds the app once,
  2. for each (scenario, device) cell: starts the mock gateway pre-seeded with
     that scenario, seeds a paired credential, launches, screenshots, and
     scores the screenshot with ui_metrics.py,
  3. prints an aggregate table + a single mean overall score.

That mean is the hill to climb: a layout change is an improvement only if it
raises the mean across the whole matrix, not just one lucky screen.

Beyond pixels, each cell also records best-effort *runtime* metrics
(cold_launch_ms, first_frame_ms) measured around `xcrun simctl launch`, in the
schema reward/scorers/perf.py consumes. When measurement fails the cell simply
omits the runtime dict and the reward degrades gracefully to "unavailable".

The matrix axes are device x Dynamic Type size x scenario: by default a large
phone and an SE-class small phone, plus an accessibility text-size variant on
the primary device (via `simctl ui ... content_size`), so layout robustness is
measured against real size pressure, not just one happy path.

Usage:
  python3 ui_matrix.py [--devices "iPhone 17,iPhone SE (3rd generation)"] \
      [--scenarios empty,short,tool,long,code] \
      [--a11y-size accessibility-large] [--no-perf] [--out DIR] [--json]
  python3 ui_matrix.py --baseline-json before.json --candidate-json after.json
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
IOS = HERE.parent
BUNDLE = "com.jcode.mobile"
APP = IOS / ".build-ios/Build/Products/Debug-iphonesimulator/JCodeMobile.app"
PORT = 7643
TOKEN = "mocktoken0123456789abcdef"
CRED = ('[{"host":"127.0.0.1","port":7643,"token":"%s","serverName":'
        '"mock-jcode","serverVersion":"mock-0.32.0","pairedAt":770000000}]' % TOKEN)

# Default matrix axes. The SE-class device exercises the small-screen end;
# the a11y content size exercises Dynamic Type pressure on the primary device.
DEFAULT_DEVICES = "iPhone 17,iPhone SE (3rd generation)"
DEFAULT_CONTENT_SIZE = "large"          # iOS default Dynamic Type category
DEFAULT_A11Y_SIZE = "accessibility-large"

# App background token (mirrors Theme.swift) used for first-frame detection.
APP_BG = (0x0F, 0x0F, 0x14)
# A frame "is the app" once this fraction of pixels sits near the background.
FIRST_FRAME_BG_FRAC = 0.30
# Give the app this long (total, from launch) before giving up on first frame.
FIRST_FRAME_TIMEOUT_S = 12.0
# Keep total post-launch settle constant so screenshots stay comparable.
SETTLE_TOTAL_S = 5.0

sys.path.insert(0, str(HERE))
import ui_metrics  # noqa: E402


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, text=True, capture_output=True, **kw)


def build_app(device):
    sh("xcodegen generate", cwd=IOS)
    r = sh(
        "xcodebuild build -project JCodeMobile.xcodeproj -scheme JCodeMobile "
        f"-destination 'platform=iOS Simulator,name={device}' "
        "-derivedDataPath .build-ios",
        cwd=IOS,
    )
    if "BUILD SUCCEEDED" not in r.stdout:
        print(r.stdout[-2000:], file=sys.stderr)
        raise SystemExit("build failed")


def boot(device):
    sh(f'xcrun simctl boot "{device}"')
    time.sleep(3)


def set_content_size(device, size):
    """Set the simulator's Dynamic Type category. Returns True on success."""
    r = sh(f'xcrun simctl ui "{device}" content_size {size}')
    ok = r.returncode == 0
    if not ok:
        print(f"warning: content_size {size} on {device} failed: "
              f"{(r.stderr or r.stdout).strip()}", file=sys.stderr)
    return ok


def start_gateway(scenario):
    sh("pkill -f mock_gateway.py")
    time.sleep(0.4)
    # background process; inherit no pipes so it stays alive
    return subprocess.Popen(
        [sys.executable, str(HERE / "mock_gateway.py"),
         "--port", str(PORT), "--host", "127.0.0.1", "--scenario", scenario],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _app_bg_frac(png_path):
    """Fraction of pixels close to the app background color (0..1)."""
    try:
        import numpy as np
        from PIL import Image
        arr = np.asarray(Image.open(png_path).convert("RGB"), dtype=np.float64)
        bg = np.array(APP_BG, dtype=np.float64)
        dist = np.sqrt(((arr - bg) ** 2).sum(axis=2))
        return float((dist < 28.0).mean())
    except Exception:
        return 0.0


def measure_first_frame(device, t_launch):
    """Poll screenshots until the app's background dominates the screen.

    Returns elapsed ms from launch, or None. Includes screenshot-capture
    latency, so treat it as a harness-relative (but consistent) measure.
    """
    deadline = t_launch + FIRST_FRAME_TIMEOUT_S
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        probe = tf.name
    try:
        while time.monotonic() < deadline:
            r = sh(f'xcrun simctl io "{device}" screenshot "{probe}"')
            if r.returncode == 0 and _app_bg_frac(probe) >= FIRST_FRAME_BG_FRAC:
                return (time.monotonic() - t_launch) * 1000.0
            time.sleep(0.15)
    finally:
        try:
            os.unlink(probe)
        except OSError:
            pass
    return None


def seed_and_launch(device, collect_perf=True):
    """Install fresh, seed the credential, cold-launch, and (best-effort) time it.

    Returns a runtime metrics dict in the reward/scorers/perf.py schema, or {}
    when nothing could be measured. The uninstall/install makes every launch a
    true cold launch, so the timing is comparable across cells.
    """
    sh(f'xcrun simctl uninstall "{device}" {BUNDLE}')
    sh(f'xcrun simctl install "{device}" "{APP}"')
    container = sh(
        f'xcrun simctl get_app_container "{device}" {BUNDLE} data'
    ).stdout.strip()
    appsup = Path(container) / "Library/Application Support"
    appsup.mkdir(parents=True, exist_ok=True)
    (appsup / "jcode-servers.json").write_text(CRED + "\n")

    runtime = {}
    t0 = time.monotonic()
    r = sh(f'xcrun simctl launch "{device}" {BUNDLE}')
    launched_ok = r.returncode == 0
    if collect_perf and launched_ok:
        # Time for simctl to spawn the process and hand back a pid: the
        # closest cheap analogue of "cold launch to process ready".
        runtime["cold_launch_ms"] = round((time.monotonic() - t0) * 1000.0, 1)
        ff = measure_first_frame(device, t0)
        if ff is not None:
            runtime["first_frame_ms"] = round(ff, 1)
    # Keep total settle constant so transcript streaming finishes and shots
    # stay comparable regardless of how long first-frame polling took.
    remaining = SETTLE_TOTAL_S - (time.monotonic() - t0)
    if remaining > 0:
        time.sleep(remaining)
    return runtime


def screenshot(device, path):
    sh(f'xcrun simctl io "{device}" screenshot "{path}"')


def device_scale(device):
    """Device pixels per point. SE/mini-class phones and iPads are 2x."""
    d = device.lower()
    if "iphone se" in d or "mini" in d or "ipad" in d:
        return 2
    return 3


def run_matrix(devices, scenarios, out_dir, a11y_size=DEFAULT_A11Y_SIZE,
               collect_perf=True):
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    first_device = devices[0]
    build_app(first_device)

    # Cell plan: every device at the default Dynamic Type size, plus the
    # primary device again at the accessibility size (if requested).
    plan = [(d, DEFAULT_CONTENT_SIZE) for d in devices]
    if a11y_size:
        plan.append((first_device, a11y_size))

    booted = set()
    for device, content_size in plan:
        if device not in booted:
            boot(device)
            booted.add(device)
        scale = device_scale(device)
        size_ok = set_content_size(device, content_size)
        if content_size != DEFAULT_CONTENT_SIZE and not size_ok:
            print(f"skipping {device} @ {content_size} (unsupported)",
                  file=sys.stderr)
            continue
        for scenario in scenarios:
            gw = start_gateway(scenario)
            try:
                runtime = seed_and_launch(device, collect_perf=collect_perf)
                shot = out_dir / (f"{slug(device)}__{slug(content_size)}"
                                  f"__{scenario}.png")
                screenshot(device, str(shot))
                card = ui_metrics.analyze(str(shot), scale=scale)
                row = {
                    "device": device, "scenario": scenario,
                    "content_size": content_size,
                    "scale": scale,
                    "shot": str(shot),
                    "overall": card.overall, "space": card.space,
                    "consistency": card.consistency,
                    "legibility": card.legibility, "rhythm": card.rhythm,
                    "fill_ratio": card.fill_ratio,
                    "dead_zone_frac": card.dead_zone_frac,
                }
                if runtime:
                    row["runtime"] = runtime
                results.append(row)
            finally:
                gw.terminate()
        # Never leave a booted simulator stuck on an accessibility size.
        if content_size != DEFAULT_CONTENT_SIZE:
            set_content_size(device, DEFAULT_CONTENT_SIZE)
    sh("pkill -f mock_gateway.py")
    return results


def slug(s):
    return s.replace(" ", "-").replace("(", "").replace(")", "")


def print_table(results):
    print(f"{'device':22} {'size':14} {'scenario':9} {'ovr':>5} {'spc':>5} "
          f"{'fill':>5} {'dead':>5} {'launch':>7} {'frame':>7}")
    print("-" * 92)
    for r in results:
        rt = r.get("runtime") or {}
        launch = f"{rt['cold_launch_ms']:.0f}ms" if "cold_launch_ms" in rt else "-"
        frame = f"{rt['first_frame_ms']:.0f}ms" if "first_frame_ms" in rt else "-"
        size = r.get("content_size", DEFAULT_CONTENT_SIZE)
        size = size.replace("accessibility", "a11y")
        print(f"{r['device'][:22]:22} {size[:14]:14} {r['scenario']:9} "
              f"{r['overall']:5.1f} {r['space']:5.1f} "
              f"{r['fill_ratio']:5.2f} {r['dead_zone_frac']:5.2f} "
              f"{launch:>7} {frame:>7}")
    print("-" * 92)
    mean = sum(r["overall"] for r in results) / max(1, len(results))
    worst = min(results, key=lambda r: r["overall"]) if results else None
    print(f"MEAN overall: {mean:5.1f}")
    if worst:
        print(f"WORST cell:   {worst['overall']:.1f}  "
              f"({worst['device']} / "
              f"{worst.get('content_size', DEFAULT_CONTENT_SIZE)} / "
              f"{worst['scenario']})")
    return mean


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--devices", default=DEFAULT_DEVICES)
    ap.add_argument("--scenarios", default="empty,short,tool,long,code")
    ap.add_argument("--a11y-size", default=DEFAULT_A11Y_SIZE,
                    help="Dynamic Type category to re-run the primary device "
                         "at (empty string disables the variant)")
    ap.add_argument("--no-perf", action="store_true",
                    help="skip runtime (launch/first-frame) measurement")
    ap.add_argument("--out", default=str(Path(os.environ.get("TMPDIR", "/tmp"))
                                         / "jcode-ui-matrix"))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--baseline-json")
    ap.add_argument("--candidate-json")
    args = ap.parse_args()

    if args.baseline_json and args.candidate_json:
        a = json.loads(Path(args.baseline_json).read_text())
        b = json.loads(Path(args.candidate_json).read_text())
        am = sum(r["overall"] for r in a) / max(1, len(a))
        bm = sum(r["overall"] for r in b) / max(1, len(b))
        print(f"baseline  mean {am:5.1f}")
        print(f"candidate mean {bm:5.1f}")
        print(f"delta     {'+' if bm >= am else ''}{bm - am:.1f}")
        sys.exit(0 if bm >= am - 0.5 else 1)

    devices = [d.strip() for d in args.devices.split(",") if d.strip()]
    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    out_dir = Path(args.out)
    results = run_matrix(devices, scenarios, out_dir,
                         a11y_size=args.a11y_size.strip(),
                         collect_perf=not args.no_perf)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print_table(results)
        print(f"\nscreenshots: {out_dir}")


if __name__ == "__main__":
    main()
