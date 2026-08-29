"""Exercise the custom sidequest mechanics: invite, lap, lost dog, and getting rejected."""
import sys, math
from playwright.sync_api import sync_playwright

FAILS = []
def check(name, ok, extra=""):
    print(("  PASS " if ok else "  FAIL ") + name + (f"  {extra}" if extra else ""))
    if not ok: FAILS.append(name)

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=swiftshader","--enable-unsafe-swiftshader","--disable-gpu-sandbox","--no-sandbox"])
    pg = b.new_page(viewport={"width":900,"height":560})
    errs = []
    pg.on("console", lambda m: errs.append(f"{m.type}: {m.text}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    pg.goto("http://127.0.0.1:8809/index.html?mute=1", wait_until="load")
    pg.wait_for_timeout(5000)
    pg.evaluate("localStorage.removeItem('fw_social_v1')"); pg.reload(wait_until="load"); pg.wait_for_timeout(5000)
    pg.click("#play"); pg.wait_for_timeout(1000)

    def take(qid):
        pg.evaluate(f"() => window.__fw.walkTo(window.__fw.giverFor('{qid}'))")
        pg.wait_for_timeout(400); pg.keyboard.press("f"); pg.wait_for_timeout(400)
        pg.keyboard.press("1"); pg.wait_for_timeout(1200)

    print("\n-- invite: 'come watch the busker' appears only while that quest runs --")
    pg.evaluate("""() => { const f=window.__fw;
        f.walkTo(f.people.find(x => !x.userData.questDef && x.userData.ai.mode==='walk')); }""")
    pg.wait_for_timeout(400); pg.keyboard.press("f"); pg.wait_for_timeout(400)
    check("no invite option before the quest",
          not any("busker" in c.lower() for c in pg.evaluate("window.__fw.picks()")))
    pg.keyboard.press("q"); pg.wait_for_timeout(1300)
    take("crowd")
    check("crowd quest active", pg.evaluate("window.__fw.quests.length") == 1)
    pg.evaluate("""() => { const f=window.__fw;
        f.walkTo(f.people.find(x => !x.userData.questDef && x.userData.ai.mode==='walk')); }""")
    pg.wait_for_timeout(400); pg.keyboard.press("f"); pg.wait_for_timeout(400)
    picks = pg.evaluate("window.__fw.picks()")
    inv = [i for i,c in enumerate(picks) if "busker" in c.lower()]
    check("invite option offered mid-quest", bool(inv), str(picks))
    if inv:
        pg.keyboard.press(str(inv[0]+1)); pg.wait_for_timeout(1400)
        sent = pg.evaluate("""() => { const p = window.__fw.people.find(x=>x.userData.ai.invitedTo);
            return p ? {mode:p.userData.ai.mode, tx:p.userData.ai.target.x, tz:p.userData.ai.target.z} : null; }""")
        check("invitee is walking to the busker", sent and sent["mode"] == "walk",
              str(sent) + " busker at Augusta & Baldwin")
        if sent:
            busk = pg.evaluate("() => window.__fw.BUSKER")
            check("...and heading to the right corner of the market",
                  math.hypot(sent["tx"]-busk["x"], sent["tz"]-busk["z"]) < 3.5)
    # fast-forward the arrivals rather than waiting for three real walks
    pg.evaluate("window.__fw.EVENTS.invite += 3"); pg.wait_for_timeout(600)
    check("crowd objective clears", pg.evaluate("window.__fw.quests[0].step") >= 1)

    print("\n-- lap: a lap of Bellevue Square on the board --")
    take("lap")
    q = pg.evaluate("() => window.__fw.quests.findIndex(x=>x.def.id==='lap')")
    check("lap quest taken", q >= 0)
    # not skating: no progress no matter how you move
    pg.evaluate("""() => { const f=window.__fw; f.state.skating=false;
        const S = f.SQUARE;
        for(let i=0;i<40;i++){ const a=i/40*6.283; f.goto(S.x+Math.cos(a)*20, S.z+Math.sin(a)*20); } }""")
    pg.wait_for_timeout(500)
    lapacc = pg.evaluate(f"() => {{ const q=window.__fw.quests[{q}]; return q.lap ? Math.abs(q.lap.acc) : 0; }}")
    check("walking a lap does not count", lapacc < 0.1, f"acc={lapacc:.2f}")
    # Skating a lap. The hero has to be genuinely rolling — facing and velocity
    # tangent to the circle — or the skate physics kills the speed and the lap
    # resets, which is exactly what it's meant to do. One step per rendered
    # frame (swiftshader is ~1 fps, so these waits are frames, not seconds).
    for i in range(30):
        a = i/29*6.283
        pg.evaluate(f"""() => {{ const f=window.__fw, a={a}, S=f.SQUARE;
            f.state.skating = true;
            f.state.pos.set(S.x+Math.cos(a)*20, 0, S.z+Math.sin(a)*20);
            f.state.facing = Math.atan2(-Math.sin(a), Math.cos(a));
            f.state.vel.set(-Math.sin(a)*8, 0, Math.cos(a)*8); }}""")
        pg.wait_for_timeout(340)
    pg.wait_for_timeout(600)
    print("   lap acc:", pg.evaluate(f"() => {{ const q=window.__fw.quests[{q}]; return q && q.lap ? q.lap.acc.toFixed(2) : 'n/a'; }}"))
    step = pg.evaluate(f"() => window.__fw.quests[{q}] ? window.__fw.quests[{q}].step : 1")
    check("skating a full lap completes it", step >= 1, f"step={step}")

    print("\n-- lost dog --")
    take("lostdog")
    qi = pg.evaluate("() => window.__fw.quests.findIndex(x=>x.def.id==='lostdog')")
    # Park the loose dog just outside the gate first: at ~1 fps the sim only
    # advances 0.05 s a frame, so a dog walking the length of the park would
    # take thousands of frames. Its speed is not what's under test — the
    # find -> follow -> penned handoff is.
    pg.evaluate("""() => { const f=window.__fw, R = f.DOG_RUN;
        const d = f.dogs.find(x=>!x.userData.ai.penned);
        const gx = R.x, gz = R.z - R.d/2 - 2.4;
        d.position.set(gx, 0, gz); d.userData.ai.pos.set(gx, 0, gz);
        f.goto(gx, gz - 1.6); window.__d = d; window.__gate = {x:gx, z:gz}; }""")
    pg.wait_for_timeout(900)
    check("dog follows once you reach it", pg.evaluate("!!window.__d.userData.ai.followHero"))
    # walk it in through the gate
    for _ in range(60):
        pg.evaluate("() => { const R = window.__fw.DOG_RUN; window.__fw.goto(R.x, R.z); }")
        if pg.evaluate("window.__d.userData.ai.penned"): break
        pg.wait_for_timeout(250)
    penned = pg.evaluate("() => ({pen: window.__d.userData.ai.penned, follow: !!window.__d.userData.ai.followHero})")
    check("dog ends up penned in the off-leash corner", penned["pen"] and not penned["follow"], str(penned))

    print("\n-- rejection --")
    pg.evaluate("""() => { const f=window.__fw;
        f.walkTo(f.people.find(x => !x.userData.questDef && x.userData.ai.mode==='sit')); }""")
    pg.wait_for_timeout(400); pg.keyboard.press("f"); pg.wait_for_timeout(400)
    check("you can talk to somebody sat down", pg.evaluate("document.getElementById('convo').classList.contains('open')"))
    # Keep picking the worst tone on offer until they bail. A topic only offers
    # three of the five tones, so the one they hate is often not among them —
    # fall back to anything that is not the tone they love, or this loop breaks
    # out with the conversation still open and the next check fails on nothing.
    for _ in range(6):
        i = pg.evaluate("""() => { const c=window.__fw.convo; if(!c.picks) return -1;
            const hate = c.picks.findIndex(p=>p.tone===c.per.hates);
            return hate >= 0 ? hate : c.picks.findIndex(p=>p.tone && p.tone!==c.per.loves); }""")
        if i < 0: break
        pg.keyboard.press(str(i+1)); pg.wait_for_timeout(900)
    st = pg.evaluate("() => ({rap: window.__fw.convo.per ? window.__fw.convo.per.rapport : null, act: window.__fw.convo.active})")
    check("wrong tone tanks it and they leave", (st["rap"] is None) or st["rap"] <= 0 or not st["act"], str(st))
    pg.wait_for_timeout(2200)
    check("everyone goes back to normal afterwards",
          pg.evaluate("() => window.__fw.people.every(p => !p.userData.ai.convo)"))

    print("\nconsole errors:", errs[:6] if errs else "none")
    if errs: FAILS.append("console errors")
    b.close()

print("\n" + ("ALL PASS" if not FAILS else "FAILED: " + ", ".join(FAILS)))
sys.exit(1 if FAILS else 0)
