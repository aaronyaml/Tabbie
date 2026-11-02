"""Helpers for driving Tabbie's UI with browser-harness (see docs/testing/UI_TESTING.md).

Load inside a harness call:
    browser-harness <<'PY'
    exec(open("docs/testing/ui_helpers.py").read())
    go(BASE + "/kanban"); shot("kanban")
    PY

Screenshots go to state/uitest/shots/ (git-ignored). Nothing here stores or prints a password.
"""
import os
import time

ROOT = os.environ.get("TABBIE_HOME", os.path.expanduser("~/hermes"))
SHOTS = os.path.join(ROOT, "state", "uitest", "shots")
os.makedirs(SHOTS, exist_ok=True)
BASE = os.environ.get("TABBIE_UI", "http://127.0.0.1:8788")  # Caddy's local port: full Tabbie, a local address

# Records JS errors, console.error and failed fetches on the page; read them with errs().
SPY = """(()=>{if(window.__hxSpy)return 1;window.__hxSpy=1;window.__errs=[];
const add=(m)=>window.__errs.push(String(m).slice(0,300));
addEventListener('error',e=>add('JS '+(e.message||e)+' @'+(e.filename||'')+':'+(e.lineno||'')));
addEventListener('unhandledrejection',e=>add('PROMISE '+(e.reason&&e.reason.message||e.reason)));
const ce=console.error;console.error=function(...a){add('CONSOLE '+a.map(x=>x&&x.message||String(x)).join(' '));return ce.apply(this,a)};
const f=window.fetch;window.fetch=async function(u,o){try{const r=await f.apply(this,arguments);if(!r.ok)add('HTTP '+r.status+' '+(o&&o.method||'GET')+' '+String(u&&u.url||u).slice(0,140));return r}catch(e){add('FETCHFAIL '+String(u).slice(0,140)+' '+e);throw e}};
return 1})()"""


def spy():
    js(SPY)


def errs():
    return js("const e=window.__errs||[];window.__errs=[];return e") or []


def shot(name):
    capture_screenshot(os.path.join(SHOTS, f"{name}.png"))


def rail(label):
    """Click a left-rail button by its aria-label (Chat, Motion, Projects, Kanban, Settings…)."""
    js(f"document.querySelector('button[aria-label=\"{label}\"]').click(); return 1")


def set_value(selector, text):
    """Fill an input without typing (the harness's fill_input/type paths can double characters)."""
    js("const e=document.querySelector(%r);e.value=%r;e.dispatchEvent(new Event('input',{bubbles:true}));return 1"
       % (selector, text))


def chip():
    """The composer's model chip text."""
    return js("const b=[...document.querySelectorAll('button')].find(b=>/Opus|Sonnet|GPT|Haiku|Fable|Codex/"
              ".test(b.textContent)&&b.offsetParent&&b.getBoundingClientRect().top>innerHeight-220);"
              "return b?b.textContent.trim():null")


def queue_count():
    # SESSION_QUEUES is a top-level const in upstream ui.js, not on window: use its functions.
    return js("return getQueuedSessionCount(S.session.session_id)")


def go(url, wait=4):
    goto_url(url)
    wait_for_load()
    time.sleep(wait)
    spy()
    if STANDALONE:
        js(UNWRAP)
        time.sleep(0.4)


def reload(wait=4):
    go(page_info()["url"], wait)


# ── iPhone 15 Pro Max as a home-screen app ───────────────────────────────────────────────────────
STANDALONE = False
# Make the page believe it's an installed app (navigator.standalone, matchMedia display-mode) before load.
PRE = r"""Object.defineProperty(navigator,'standalone',{get:()=>true});
const _mm=window.matchMedia.bind(window);
window.matchMedia=(q)=>{ if(/display-mode:\s*(standalone|fullscreen)/.test(q)) return _mm('(min-width:0px)');
  if(/display-mode:\s*browser/.test(q)) return _mm('(max-width:0px)'); return _mm(q); };"""
# Chrome can't emulate display-mode in CSS: copy every @media (display-mode: standalone) rule out unwrapped.
UNWRAP = r"""(()=>{let css='';const walk=(rules)=>{for(const r of rules){if(r.media&&/display-mode:\s*(standalone|fullscreen)/.test(r.media.mediaText)){for(const c of r.cssRules)css+=c.cssText+'\n';} else if(r.cssRules)walk(r.cssRules);}};
for(const s of document.styleSheets){try{walk(s.cssRules)}catch(_){}}
let st=document.getElementById('hxStandaloneSim');if(!st){st=document.createElement('style');st.id='hxStandaloneSim';document.head.appendChild(st);}st.textContent=css;return css.length})()"""
# Dynamic Island, status-bar clock and home indicator drawn on top, to see what they'd cover.
NOTCH = """(()=>{if(document.getElementById('hxNotchSim'))return 1;const d=document.createElement('div');d.id='hxNotchSim';
d.style.cssText='position:fixed;inset:0;pointer-events:none;z-index:2147483647';
d.innerHTML='<div style="position:absolute;top:11px;left:50%;transform:translateX(-50%);width:126px;height:37px;border-radius:20px;background:#000"></div>'+
'<div style="position:absolute;top:16px;left:30px;font:600 16px -apple-system;color:#888">9:41</div>'+
'<div style="position:absolute;bottom:8px;left:50%;transform:translateX(-50%);width:140px;height:5px;border-radius:3px;background:#888"></div>';
document.documentElement.appendChild(d);return 1})()"""


def iphone(standalone=True):
    """430x932 @3x, iOS Safari UA, safe-area insets 59 top / 34 bottom (Dynamic Island + home bar)."""
    global STANDALONE
    cdp("Emulation.setDeviceMetricsOverride", width=430, height=932, deviceScaleFactor=3, mobile=True)
    cdp("Emulation.setSafeAreaInsetsOverride", insets={"top": 59, "bottom": 34, "left": 0, "right": 0})
    cdp("Emulation.setUserAgentOverride", userAgent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
    if standalone:
        cdp("Page.addScriptToEvaluateOnNewDocument", source=PRE)
    STANDALONE = standalone


def desktop():
    global STANDALONE
    cdp("Emulation.clearDeviceMetricsOverride")
    STANDALONE = False  # the PRE script stays for this tab: open a new tab for clean desktop runs


def pshot(name):
    """Phone screenshot with the notch overlay."""
    js(NOTCH)
    time.sleep(0.2)
    shot(name)


def reload_css(name):
    """Preview an edited stylesheet without bumping extensions.json (e.g. reload_css('apple-skin'))."""
    js(r"""const l=[...document.querySelectorAll('link[rel=stylesheet]')].find(l=>l.href.includes('%s'));
      l.href=l.href.replace(/\?v=[^&]+/, '?v=p'+Date.now()); return 1""" % name)
    time.sleep(1.5)
