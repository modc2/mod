"""Drive the social layer headless: talk, flirt, get a number, take + finish a quest."""
import sys
from playwright.sync_api import sync_playwright

FAILS = []
def check(name, ok, extra=""):
    print(("  PASS " if ok else "  FAIL ") + name + (f"  {extra}" if extra else ""))
    if not ok: FAILS.append(name)

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=swiftshader","--enable-unsafe-swiftshader","--disable-gpu-sandbox","--no-sandbox"])
    pg = b.new_page(viewport={"width":1000,"height":620})
    errs = []
    pg.on("console", lambda m: errs.append(f"{m.type}: {m.text}") if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    pg.goto("http://127.0.0.1:8809/index.html?mute=1", wait_until="load")
    pg.wait_for_timeout(5000)
    pg.evaluate("localStorage.removeItem('fw_social_v1')")
    pg.click("#play")
    pg.wait_for_timeout(1200)

    print("\n-- 1. walk up to a stranger --")
    pg.evaluate("""() => { const f=window.__fw;
        const p = f.people.find(x => !x.userData.questDef && x.userData.ai.mode==='walk');
        f.walkTo(p); window.__t = p; }""")
    pg.wait_for_timeout(500)
    prompt = pg.evaluate("document.getElementById('prompt').className")
    check("interaction prompt shows", "show" in prompt, prompt)

    pg.keyboard.press("f")
    pg.wait_for_timeout(400)
    st = pg.evaluate("""() => ({ open: document.getElementById('convo').classList.contains('open'),
        name: document.getElementById('cvName').textContent,
        job: document.getElementById('cvJob').textContent,
        line: document.getElementById('cvLine').innerText.slice(0,90),
        picks: window.__fw.picks(), locked: !!document.pointerLockElement })""")
    check("dialogue opens", st["open"])
    check("NPC has a name + job", len(st["name"]) > 1 and len(st["job"]) > 2, f'{st["name"]} {st["job"]}')
    check("opener + topic on screen", len(st["line"]) > 20)
    check("choices offered (3 + quit)", len(st["picks"]) >= 4, str(len(st["picks"])))
    check("pointer released for the cursor", not st["locked"])
    print("   line:", st["line"].replace("\n"," "))
    for c in st["picks"]: print("   -", c)

    print("\n-- 2. tones move rapport --")
    tones = pg.evaluate("() => ({loves: window.__fw.convo.per.loves, hates: window.__fw.convo.per.hates})")
    print("   loves:", tones["loves"], " hates:", tones["hates"])
    r0 = pg.evaluate("window.__fw.convo.per.rapport")
    # pick whichever visible choice matches their favourite tone, else choice 1
    idx = pg.evaluate("""() => { const c=window.__fw.convo; const i=c.picks.findIndex(p=>p.tone===c.per.loves);
                                 return i>=0?i:0; }""")
    pg.keyboard.press(str(idx+1))
    pg.wait_for_timeout(300)
    r1 = pg.evaluate("window.__fw.convo.per.rapport")
    check("rapport moves on a choice", r1 != r0, f"{r0} -> {r1}")
    check("conversation continues", pg.evaluate("window.__fw.picks().length") >= 4)

    print("\n-- 3. get the number --")
    pg.evaluate("window.__fw.convo.per.rapport = 85; window.__fw.convo.round = 3;")
    pg.keyboard.press("1")   # one more exchange so the ask row is rendered
    pg.wait_for_timeout(300)
    picks = pg.evaluate("window.__fw.picks()")
    ask = [i for i,c in enumerate(picks) if "number" in c.lower()]
    check("'can I get your number?' unlocks at high rapport", bool(ask), str(picks))
    if ask:
        pg.evaluate("window.__fw.convo.per.rapport = 100")   # make the roll a near-certainty
        pg.keyboard.press(str(ask[0]+1))
        pg.wait_for_timeout(400)
        got = pg.evaluate("window.__fw.social.contacts")
        check("number lands in contacts", len(got) == 1, str(got))
        if got: print("   contact:", got[0])
    pg.keyboard.press("q")
    pg.wait_for_timeout(1400)
    check("dialogue closes", not pg.evaluate("document.getElementById('convo').classList.contains('open')"))
    check("NPC resumes its life", pg.evaluate("!window.__t.userData.ai.convo"))

    print("\n-- 4. phone --")
    pg.keyboard.press("p")
    pg.wait_for_timeout(300)
    check("contacts panel opens", pg.evaluate("document.getElementById('phone').classList.contains('open')"))
    check("contact rendered", pg.evaluate("document.getElementById('phBody').innerText").count("555") == 1,
          pg.evaluate("document.getElementById('phBody').innerText")[:80].replace("\n"," / "))
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)

    print("\n-- 5. sidequest: take one, finish it, hand it in --")
    pg.evaluate("() => { const g = window.__fw.giverFor('pigeons'); window.__fw.walkTo(g); }")
    pg.wait_for_timeout(400)
    pg.keyboard.press("f")
    pg.wait_for_timeout(400)
    brief = pg.evaluate("document.getElementById('cvLine').innerText.slice(0,60)")
    check("quest giver briefs you", len(brief) > 20, brief.replace("\n", " "))
    pg.keyboard.press("1")   # I'm in
    pg.wait_for_timeout(1200)
    check("quest is active", pg.evaluate("window.__fw.quests.length") == 1)
    check("tracker panel visible", not pg.evaluate("document.getElementById('quest').classList.contains('hidden')"))
    print("   tracker:", pg.evaluate("document.getElementById('qlist').innerText").replace("\n"," | "))
    check("beacon in the world", pg.evaluate("""() => { let v=false;
        window.__fw.scene.traverse(o=>{ if(o.type==='Group' && o.children[0] && o.children[0].geometry &&
          o.children[0].geometry.type==='OctahedronGeometry' && o.visible) v=true; }); return v; }""") is not None)

    # simulate the six flocks the quest wants
    pg.evaluate("window.__fw.EVENTS.flush += 6")
    pg.wait_for_timeout(600)
    check("objective completes", pg.evaluate("window.__fw.quests[0].step") >= 1,
          str(pg.evaluate("window.__fw.quests[0].step")))
    pg.evaluate("() => { const g = window.__fw.giverFor('pigeons'); window.__fw.walkTo(g); }")
    pg.wait_for_timeout(400)
    pg.keyboard.press("f")
    pg.wait_for_timeout(500)
    done = pg.evaluate("() => ({done: window.__fw.social.done, rep: window.__fw.social.rep, open: window.__fw.quests.length})")
    check("quest hands in for rep", done["done"].get("pigeons") is True and done["rep"] == 2 and done["open"] == 0, str(done))
    pg.keyboard.press("q")
    pg.wait_for_timeout(1200)

    print("\n-- 6. movement is not broken --")
    pg.evaluate("window.__fw.goto(0,-30)")
    before = pg.evaluate("window.__fw.state.pos.z")
    pg.keyboard.down("w"); pg.wait_for_timeout(1400); pg.keyboard.up("w")
    after = pg.evaluate("window.__fw.state.pos.z")
    check("player still moves after all that", abs(after-before) > 0.5, f"{before:.1f} -> {after:.1f}")

    print("\n-- 7. persistence --")
    pg.reload(wait_until="load"); pg.wait_for_timeout(5000)
    keep = pg.evaluate("() => ({c: window.__fw.social.contacts.length, rep: window.__fw.social.rep})")
    check("number + rep survive a reload", keep["c"] == 1 and keep["rep"] == 2, str(keep))

    pg.click("#play"); pg.wait_for_timeout(800)
    pg.evaluate("() => { const g=window.__fw.giverFor('wingman'); window.__fw.walkTo(g); }")
    pg.wait_for_timeout(600)
    pg.keyboard.press("f"); pg.wait_for_timeout(600)
    pg.screenshot(path="/tmp/fw_convo.png")
    print("\n  shot -> /tmp/fw_convo.png")

    print("\nconsole errors:", errs[:6] if errs else "none")
    if errs: FAILS.append("console errors")
    b.close()

print("\n" + ("ALL PASS" if not FAILS else "FAILED: " + ", ".join(FAILS)))
sys.exit(1 if FAILS else 0)
