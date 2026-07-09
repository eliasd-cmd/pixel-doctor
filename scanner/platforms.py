"""Detección por plataforma a partir del resultado del escáner.

Para cada plataforma se determina:
- library_loaded: si la librería JS se cargó por red.
- in_html: si el código está presente en el HTML (aunque no cargara).
- ids: identificadores detectados (GTM-, G-, AW-, pixel IDs, etc.).
- events: hits/eventos reales enviados a los servidores de la plataforma.
- failed / errors: peticiones de tracking bloqueadas o con error HTTP.
"""

import json
import re
from urllib.parse import urlparse, parse_qsl


def _q(url):
    try:
        return dict(parse_qsl(urlparse(url).query, keep_blank_values=True))
    except Exception:
        return {}


def _body_lines(post_data):
    if not post_data:
        return []
    return [ln for ln in post_data.splitlines() if ln.strip()]


def _multipart(post_data):
    """Extrae pares nombre→valor de un cuerpo multipart/form-data."""
    out = {}
    if not post_data or "form-data" not in post_data:
        return out
    for m in re.finditer(
            r'name="([^"]+)"\r?\n\r?\n(.*?)\r?\n--', post_data, re.S):
        out[m.group(1)] = m.group(2).strip()[:500]
    return out


def _ev(platform, name, pid, req, params=None):
    return {
        "platform": platform,
        "event": name or "(sin nombre)",
        "id": pid or "",
        "url": req["url"][:300],
        "status": req["status"],
        "failure": req["failure"],
        "phase": req.get("phase", ""),
        "ts": req.get("ts"),
        "params": params or {},
    }


# ---------------------------------------------------------------- GA4 ------

# /g/collect en cualquier dominio: cubre google-analytics.com, regiones UE
# (region1.google-analytics.com) y GTM server-side en dominio propio.
GA4_HIT_RE = re.compile(r"/g/collect\?")
GA4_LIB_RE = re.compile(r"googletagmanager\.com/gtag/js\?.*\bid=(G-[A-Z0-9]+)")


def detect_ga4(requests, html, js):
    d = {"key": "ga4", "name": "Google Analytics 4", "ids": set(),
         "library_loaded": False, "in_html": False, "events": [],
         "server_side_hosts": set()}
    for r in requests:
        m = GA4_LIB_RE.search(r["url"])
        if m:
            d["library_loaded"] = True
            d["ids"].add(m.group(1))
        if GA4_HIT_RE.search(r["url"]):
            q = _q(r["url"])
            if q.get("v") != "2":
                continue
            host = urlparse(r["url"]).netloc
            if "google-analytics.com" not in host and "analytics.google.com" not in host:
                d["server_side_hosts"].add(host)
            tid = q.get("tid", "")
            if tid:
                d["ids"].add(tid)
            interesting = {k: q[k] for k in q
                           if k in ("gcs", "gcd", "dl", "sid", "cid", "dt", "_et")
                           or k.startswith(("ep.", "epn.", "up."))}
            if q.get("en"):
                d["events"].append(_ev("ga4", q["en"], tid, r, {**interesting}))
            body_evs = 0
            for line in _body_lines(r.get("post_data")):
                bp = dict(parse_qsl(line, keep_blank_values=True))
                if bp.get("en"):
                    extra = {k: bp[k] for k in bp
                             if k == "_et" or k.startswith(("ep.", "epn.", "up."))}
                    d["events"].append(_ev("ga4", bp["en"], tid, r,
                                           {**interesting, **extra}))
                    body_evs += 1
            if not q.get("en") and not body_evs:
                # Hit sin nombre de evento: ping de config/consent-mode, no un
                # page_view real (no debe contar para la regla de duplicados).
                name = "ping consent-mode" if "gcu" in q or q.get("npa") == "1" \
                    else "config/ping (sin en)"
                d["events"].append(_ev("ga4", name, tid, r, interesting))
    for m in re.finditer(r"\bG-[A-Z0-9]{6,}\b", html or ""):
        d["ids"].add(m.group(0))
        d["in_html"] = True
    d["server_side_hosts"] = sorted(d["server_side_hosts"])
    return d


# ------------------------------------------------------ Universal Analytics

def detect_ua(requests, html, js):
    d = {"key": "ua", "name": "Universal Analytics (obsoleto)", "ids": set(),
         "library_loaded": False, "in_html": False, "events": []}
    for r in requests:
        if "google-analytics.com/analytics.js" in r["url"] or \
           "google-analytics.com/ga.js" in r["url"]:
            d["library_loaded"] = True
        if re.search(r"google-analytics\.com/(j/)?collect", r["url"]) and \
                not GA4_HIT_RE.search(r["url"]):
            q = _q(r["url"])
            tid = q.get("tid", "")
            if tid.startswith("UA-"):
                d["ids"].add(tid)
                d["events"].append(_ev("ua", q.get("t", "hit"), tid, r))
    for m in re.finditer(r"\bUA-\d{4,}-\d+\b", html or ""):
        d["ids"].add(m.group(0))
        d["in_html"] = True
    return d


# ---------------------------------------------------------------- GTM ------

def detect_gtm(requests, html, js):
    d = {"key": "gtm", "name": "Google Tag Manager", "ids": set(),
         "library_loaded": False, "in_html": False, "events": [],
         "containers_loaded": []}
    for r in requests:
        m = re.search(r"googletagmanager\.com/gtm\.js\?.*\bid=(GTM-[A-Z0-9]+)", r["url"])
        if m:
            d["library_loaded"] = True
            d["ids"].add(m.group(1))
            if m.group(1) not in d["containers_loaded"]:
                d["containers_loaded"].append(m.group(1))
    for m in re.finditer(r"\bGTM-[A-Z0-9]{4,}\b", html or ""):
        d["ids"].add(m.group(0))
        d["in_html"] = True
    gtm_keys = ((js or {}).get("globals") or {}).get("google_tag_manager") or []
    for k in gtm_keys:
        if k.startswith("GTM-"):
            d["ids"].add(k)
    return d


# ---------------------------------------------------------- Google Ads ----

ADS_HIT_RE = re.compile(
    r"(googleadservices\.com/pagead/conversion|"
    r"google\.com/pagead/1p-conversion|"
    r"googleads\.g\.doubleclick\.net/pagead/viewthroughconversion|"
    r"google\.com/ccm/collect|"
    r"google\.[a-z.]+/pagead/1p-user-list)")


def detect_gads(requests, html, js):
    d = {"key": "gads", "name": "Google Ads", "ids": set(),
         "library_loaded": False, "in_html": False, "events": []}
    for r in requests:
        m = re.search(r"googletagmanager\.com/gtag/js\?.*\bid=(AW-[0-9]+)", r["url"])
        if m:
            d["library_loaded"] = True
            d["ids"].add(m.group(1))
        if ADS_HIT_RE.search(r["url"]):
            q = _q(r["url"])
            m2 = re.search(r"(?:conversion|viewthroughconversion)/(\d+)", r["url"])
            pid = ("AW-" + m2.group(1)) if m2 else q.get("tid", "")
            if pid:
                d["ids"].add(pid)
            label = q.get("label", "")
            name = "conversión" + (f" ({label})" if label else "")
            if "1p-user-list" in r["url"] or "viewthroughconversion" in r["url"]:
                name = "remarketing/page_view"
            if "ccm/collect" in r["url"]:
                name = q.get("en") or "consent-mode ping"
            d["events"].append(_ev("gads", name, pid, r,
                                   {k: q[k] for k in ("gcs", "gcd", "label", "gclid",
                                                      "gclaw", "url") if k in q}))
    for m in re.finditer(r"\bAW-\d{8,}\b", html or ""):
        d["ids"].add(m.group(0))
        d["in_html"] = True
    return d


# --------------------------------------------------------------- Meta ------

def detect_meta(requests, html, js):
    d = {"key": "meta", "name": "Meta Pixel (Facebook/Instagram)", "ids": set(),
         "library_loaded": False, "in_html": False, "events": []}
    for r in requests:
        if "connect.facebook.net" in r["url"] and "fbevents.js" in r["url"]:
            d["library_loaded"] = True
        if re.search(r"facebook\.com/tr[/?]", r["url"]):
            q = _q(r["url"])
            if r["method"] == "POST" and r.get("post_data"):
                body = r["post_data"]
                if "form-data" in body:
                    q = {**q, **_multipart(body)}
                else:
                    q = {**q, **dict(parse_qsl(body, keep_blank_values=True))}
            pid = q.get("id", "")
            if pid:
                d["ids"].add(pid)
            cd = {k: v for k, v in q.items() if k.startswith("cd[")}
            extra = {k: q[k] for k in ("eid", "fbc", "fbp") if k in q}
            d["events"].append(_ev("meta", q.get("ev", "(sin ev)"), pid, r,
                                   {**extra, **cd}))
    for m in re.finditer(r"fbq\(\s*['\"]init['\"]\s*,\s*['\"](\d{5,})['\"]", html or ""):
        d["ids"].add(m.group(1))
        d["in_html"] = True
    return d


# ----------------------------------------------------------- LinkedIn -----

def detect_linkedin(requests, html, js):
    d = {"key": "linkedin", "name": "LinkedIn Insight Tag", "ids": set(),
         "library_loaded": False, "in_html": False, "events": []}
    for r in requests:
        if "snap.licdn.com" in r["url"] and "insight" in r["url"]:
            d["library_loaded"] = True
        if re.search(r"px\.ads\.linkedin\.com/(collect|attribution_trigger)", r["url"]):
            q = _q(r["url"])
            pid = q.get("pid", "")
            if pid:
                d["ids"].add(pid)
            name = "conversión" if q.get("conversionId") else (q.get("fmt") and "page_view/collect" or "collect")
            d["events"].append(_ev("linkedin", name, pid, r,
                                   {k: q[k] for k in ("conversionId", "fmt") if k in q}))
    for m in re.finditer(r"_linkedin_partner_id\s*=\s*['\"](\d+)['\"]", html or ""):
        d["ids"].add(m.group(1))
        d["in_html"] = True
    pids = ((js or {}).get("globals") or {}).get("_linkedin_data_partner_ids")
    if isinstance(pids, list):
        for p in pids:
            d["ids"].add(str(p))
    return d


# ------------------------------------------------------------- TikTok -----

def detect_tiktok(requests, html, js):
    d = {"key": "tiktok", "name": "TikTok Pixel", "ids": set(),
         "library_loaded": False, "in_html": False, "events": []}
    for r in requests:
        m = re.search(r"analytics\.tiktok\.com/i18n/pixel/(?:events|sdk)[^?]*\?.*sdkid=([A-Z0-9]+)",
                      r["url"], re.I)
        if "analytics.tiktok.com/i18n/pixel" in r["url"]:
            d["library_loaded"] = True
        if m:
            d["ids"].add(m.group(1))
        if re.search(r"analytics\.tiktok\.com/api/v2/pixel", r["url"]):
            name, pid = "track", ""
            if r.get("post_data"):
                try:
                    body = json.loads(r["post_data"])
                    name = body.get("event") or body.get("type") or "track"
                    pid = (body.get("context", {}).get("pixel", {}) or {}).get("code", "")
                except Exception:
                    pass
            if pid:
                d["ids"].add(pid)
            d["events"].append(_ev("tiktok", name, pid, r))
    for m in re.finditer(r"ttq\.load\(\s*['\"]([A-Z0-9]{10,})['\"]", html or "", re.I):
        d["ids"].add(m.group(1))
        d["in_html"] = True
    return d


# ------------------------------------------------- Microsoft Ads (Bing) ---

def detect_bing(requests, html, js):
    d = {"key": "bing", "name": "Microsoft Ads UET (Bing)", "ids": set(),
         "library_loaded": False, "in_html": False, "events": []}
    for r in requests:
        if "bat.bing.com/bat.js" in r["url"] or "bat.bing.com/p/action" in r["url"]:
            d["library_loaded"] = True
        m = re.search(r"bat\.bing\.com(?:/p)?/action/?\d*\?", r["url"])
        if m:
            q = _q(r["url"])
            ti = q.get("ti", "")
            if ti:
                d["ids"].add(ti)
            d["events"].append(_ev("bing", q.get("evt", "pageLoad"), ti, r,
                                   {k: q[k] for k in ("ea", "el", "gv") if k in q}))
    for m in re.finditer(r"['\"]?ti['\"]?\s*:\s*['\"](\d{6,9})['\"]", html or ""):
        d["ids"].add(m.group(1))
        d["in_html"] = True
    return d


# ----------------------------------------------------- Otros píxeles ------

def detect_twitter(requests, html, js):
    d = {"key": "twitter", "name": "X/Twitter Pixel", "ids": set(),
         "library_loaded": False, "in_html": False, "events": []}
    for r in requests:
        if "static.ads-twitter.com/uwt.js" in r["url"]:
            d["library_loaded"] = True
        if re.search(r"(analytics\.twitter\.com|t\.co)/i/adsct", r["url"]):
            q = _q(r["url"])
            pid = q.get("txn_id", "")
            if pid:
                d["ids"].add(pid)
            d["events"].append(_ev("twitter", q.get("events", "pageview"), pid, r))
    for m in re.finditer(r"twq\(\s*['\"](?:config|init)['\"]\s*,\s*['\"]([a-z0-9]{5,})['\"]", html or ""):
        d["ids"].add(m.group(1))
        d["in_html"] = True
    return d


def detect_pinterest(requests, html, js):
    d = {"key": "pinterest", "name": "Pinterest Tag", "ids": set(),
         "library_loaded": False, "in_html": False, "events": []}
    for r in requests:
        if "s.pinimg.com/ct/core.js" in r["url"]:
            d["library_loaded"] = True
        if "ct.pinterest.com/v3" in r["url"]:
            q = _q(r["url"])
            pid = q.get("tid", "")
            if pid:
                d["ids"].add(pid)
            d["events"].append(_ev("pinterest", q.get("event", "pagevisit"), pid, r))
    for m in re.finditer(r"pintrk\(\s*['\"]load['\"]\s*,\s*['\"](\d{10,})['\"]", html or ""):
        d["ids"].add(m.group(1))
        d["in_html"] = True
    return d


def detect_snapchat(requests, html, js):
    d = {"key": "snapchat", "name": "Snap Pixel", "ids": set(),
         "library_loaded": False, "in_html": False, "events": []}
    for r in requests:
        if "sc-static.net/scevent" in r["url"]:
            d["library_loaded"] = True
        if "tr.snapchat.com" in r["url"]:
            d["events"].append(_ev("snapchat", "event", "", r))
    for m in re.finditer(r"snaptr\(\s*['\"]init['\"]\s*,\s*['\"]([a-f0-9-]{20,})['\"]", html or ""):
        d["ids"].add(m.group(1))
        d["in_html"] = True
    return d


def detect_hotjar(requests, html, js):
    d = {"key": "hotjar", "name": "Hotjar", "ids": set(),
         "library_loaded": False, "in_html": False, "events": []}
    for r in requests:
        m = re.search(r"static\.hotjar\.com/c/hotjar-(\d+)\.js", r["url"])
        if m:
            d["library_loaded"] = True
            d["ids"].add(m.group(1))
    for m in re.finditer(r"hjid\s*[:=]\s*(\d{5,})", html or ""):
        d["ids"].add(m.group(1))
        d["in_html"] = True
    return d


def detect_clarity(requests, html, js):
    d = {"key": "clarity", "name": "Microsoft Clarity", "ids": set(),
         "library_loaded": False, "in_html": False, "events": []}
    for r in requests:
        m = re.search(r"clarity\.ms/tag/([a-z0-9]+)", r["url"])
        if m:
            d["library_loaded"] = True
            d["ids"].add(m.group(1))
        if re.search(r"[a-z]\.clarity\.ms/collect", r["url"]):
            d["events"].append(_ev("clarity", "collect", "", r))
    return d


def detect_hubspot(requests, html, js):
    d = {"key": "hubspot", "name": "HubSpot Tracking", "ids": set(),
         "library_loaded": False, "in_html": False, "events": []}
    for r in requests:
        m = re.search(r"js(?:-eu1)?\.hs-scripts\.com/(\d+)\.js", r["url"])
        if m:
            d["library_loaded"] = True
            d["ids"].add(m.group(1))
        if "track.hubspot.com/__ptq.gif" in r["url"] or \
           "track-eu1.hubspot.com/__ptq.gif" in r["url"]:
            q = _q(r["url"])
            pid = q.get("a", "")
            if pid:
                d["ids"].add(pid)
            k = q.get("k", "")
            name = {"1": "page_view", "28": "identify/analytics"}.get(k, f"hit (k={k})")
            d["events"].append(_ev("hubspot", name, pid, r))
    for m in re.finditer(r"hs-scripts\.com/(\d+)\.js", html or ""):
        d["ids"].add(m.group(1))
        d["in_html"] = True
    return d


DETECTORS = [
    detect_gtm, detect_ga4, detect_gads, detect_ua, detect_meta,
    detect_linkedin, detect_tiktok, detect_bing, detect_twitter,
    detect_pinterest, detect_snapchat, detect_hotjar, detect_clarity,
    detect_hubspot,
]

# Plataformas de ads/analytics "principales" para el resumen
CORE_PLATFORMS = ["gtm", "ga4", "gads", "meta", "linkedin", "tiktok", "bing"]


# ------------------------------------------------------------- CMP --------

CMP_SIGNATURES = [
    ("OneTrust", ["cdn.cookielaw.org", "onetrust"]),
    ("Cookiebot", ["consent.cookiebot.com", "cookiebot"]),
    ("Didomi", ["sdk.privacy-center.org", "didomi"]),
    ("Usercentrics", ["usercentrics.eu", "usercentrics"]),
    ("CookieYes", ["cdn-cookieyes.com", "cookieyes", "cky-"]),
    ("Complianz", ["complianz", "cmplz"]),
    ("Axeptio", ["axept.io", "axeptio"]),
    ("iubenda", ["iubenda.com", "iubenda"]),
    ("TrustArc", ["trustarc.com", "truste"]),
    ("Quantcast", ["quantcast.mgr.consensu.org", "qc-cmp2"]),
    ("CookieScript", ["cookie-script.com", "cookiescript"]),
    ("HubSpot Cookie Banner", ["hs-banner.com"]),
]


def detect_cmp(scan):
    html = (scan.get("html") or "").lower()
    urls = " ".join(r["url"].lower() for r in scan.get("requests", []))
    found = []
    for name, sigs in CMP_SIGNATURES:
        if any(s in html or s in urls for s in sigs):
            found.append(name)
    g = ((scan.get("js") or {}).get("globals") or {})
    tcf = g.get("__tcfapi") == "function"
    return {"cmps": found, "tcf_api": tcf,
            "consent_clicked": scan.get("consent_click")}


def detect_all(scan):
    requests = scan.get("requests", [])
    html = scan.get("html", "")
    js = scan.get("js") or {}
    out = {}
    for fn in DETECTORS:
        d = fn(requests, html, js)
        d["ids"] = sorted(d["ids"])
        d["detected"] = bool(d["ids"] or d["library_loaded"] or d["events"] or d["in_html"])
        out[d["key"]] = d
    return out


def all_events(detections):
    evs = []
    for d in detections.values():
        evs.extend(d["events"])
    return sorted(evs, key=lambda e: (e.get("ts") or 0))


def tracking_failures(scan):
    """Peticiones de tracking bloqueadas o con error HTTP."""
    TRACK_HOSTS = ("google-analytics", "googletagmanager", "googleadservices",
                   "doubleclick", "facebook.com/tr", "connect.facebook.net",
                   "licdn.com", "px.ads.linkedin", "analytics.tiktok",
                   "bat.bing", "clarity.ms", "hotjar", "hs-scripts",
                   "track.hubspot", "ads-twitter", "pinimg.com/ct",
                   "ct.pinterest", "sc-static.net", "tr.snapchat",
                   "ccm/collect", "pagead")
    bad = []
    for r in scan.get("requests", []):
        if not any(h in r["url"] for h in TRACK_HOSTS):
            continue
        if r.get("failure"):
            bad.append({**r, "problema": f"Petición fallida: {r['failure']}"})
        elif r.get("status") and r["status"] >= 400:
            bad.append({**r, "problema": f"HTTP {r['status']}"})
    return bad
