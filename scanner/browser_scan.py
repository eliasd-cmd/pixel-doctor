"""Escáner de medición.

Carga una URL con Chromium headless (Playwright) y captura todo lo necesario
para auditar la implementación de analítica y píxeles publicitarios:

- Todas las peticiones de red (con estado HTTP, cuerpo POST y fallos).
- Snapshot de window.dataLayer y de los objetos globales de cada plataforma.
- Cookies creadas, errores de consola y errores de página.
- Fase pre/post consentimiento: opcionalmente acepta el banner de cookies
  y marca qué hits se dispararon antes y después.

Se ejecuta como módulo CLI para aislarlo del proceso de Streamlit:

    python3 -m scanner.browser_scan https://ejemplo.com --consent --json -
"""

import argparse
import json
import re
import sys
import time

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

MAX_REQUESTS = 1500
MAX_POST_DATA = 6000
MAX_HTML = 900_000
MAX_CONSOLE = 300

DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)

# Botones de "aceptar todo" de los CMP más comunes (Playwright atraviesa
# shadow DOM abiertos, por lo que también funciona con Usercentrics, etc.)
CONSENT_SELECTORS = [
    "#onetrust-accept-btn-handler",                                    # OneTrust
    "#didomi-notice-agree-button",                                     # Didomi
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",          # Cookiebot
    "#CybotCookiebotDialogBodyButtonAccept",                           # Cookiebot (variante)
    ".cky-btn-accept",                                                 # CookieYes
    "#cookiescript_accept",                                            # CookieScript
    ".cmplz-accept",                                                   # Complianz
    "#axeptio_btn_acceptAll",                                          # Axeptio
    ".iubenda-cs-accept-btn",                                          # iubenda
    "[data-cookiebanner='accept_button']",                             # Meta/custom
    "#hs-eu-confirmation-button",                                      # HubSpot banner
    "button#truste-consent-button",                                    # TrustArc
    ".qc-cmp2-summary-buttons button[mode='primary']",                 # Quantcast
    "[data-testid='uc-accept-all-button']",                            # Usercentrics
]

CONSENT_TEXT_RE = r"(?i)^(aceptar( todo| todas)?( las cookies)?|accept( all)?( cookies)?|permitir todas|agree|estoy de acuerdo|entendido|allow all|acepto)$"

# Respaldo: APIs JS oficiales de los CMP para aceptar todo cuando el banner no
# está visible o no se encuentra el botón (p. ej. Usercentrics sin UI).
CONSENT_JS_APIS = """
async () => {
  try { if (window.UC_UI && UC_UI.acceptAllConsents) {
    await UC_UI.acceptAllConsents();
    try { UC_UI.closeCMP && UC_UI.closeCMP(); } catch (e) {}
    return 'API Usercentrics'; } } catch (e) {}
  try { if (window.OneTrust && OneTrust.AllowAll) {
    OneTrust.AllowAll(); return 'API OneTrust'; } } catch (e) {}
  try { if (window.Cookiebot && Cookiebot.submitCustomConsent) {
    Cookiebot.submitCustomConsent(true, true, true); return 'API Cookiebot'; } } catch (e) {}
  try { if (window.Didomi && Didomi.setUserAgreeToAll) {
    Didomi.setUserAgreeToAll(); return 'API Didomi'; } } catch (e) {}
  try { if (typeof cmplz_accept_all === 'function') {
    cmplz_accept_all(); return 'API Complianz'; } } catch (e) {}
  try { if (window.__tcfapi && window.OptanonWrapper === undefined &&
            window.CookieScript && CookieScript.instance) {
    CookieScript.instance.acceptAllAction(); return 'API CookieScript'; } } catch (e) {}
  return null;
}
"""

# Simulación de llegada de campaña (--attribution): por plataforma, con el
# click-ID y las UTM que esa plataforma usaría de verdad. Permite verificar que
# los click-IDs sobreviven a redirecciones, generan su cookie y llegan en los hits.
ATTRIBUTION_SETS = {
    "google": {"gclid": "PXDOCTESTGCLID123",
               "utm_source": "google", "utm_medium": "cpc"},
    "meta": {"fbclid": "PXDOCTESTFBCLID123",
             "utm_source": "facebook", "utm_medium": "paid_social"},
    "linkedin": {"li_fat_id": "pxdoctest-lifatid-123",
                 "utm_source": "linkedin", "utm_medium": "paid_social"},
    "tiktok": {"ttclid": "pxdoctestttclid123",
               "utm_source": "tiktok", "utm_medium": "paid_social"},
    "bing": {"msclkid": "pxdoctestmsclkid123",
             "utm_source": "bing", "utm_medium": "cpc"},
}


def add_attribution_params(url, platforms):
    """Añade a la URL los parámetros de campaña simulados de las plataformas
    elegidas (sin pisar los que ya traiga la URL)."""
    from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
    keys = [k for k in platforms if k in ATTRIBUTION_SETS]
    add = {"utm_campaign": "test-medicion"}
    for k in keys:
        add.update({kk: vv for kk, vv in ATTRIBUTION_SETS[k].items()
                    if not kk.startswith("utm_")})
    if len(keys) == 1:  # una sola plataforma: UTMs realistas de esa fuente
        add.update({kk: vv for kk, vv in ATTRIBUTION_SETS[keys[0]].items()
                    if kk.startswith("utm_")})
    else:
        add.update({"utm_source": "pixel-doctor", "utm_medium": "auditoria"})
    parts = urlparse(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    for k, v in add.items():
        q.setdefault(k, v)
    return urlunparse(parts._replace(query=urlencode(q)))

JS_SNAPSHOT = """
() => {
  const safeItem = (o) => {
    try {
      return JSON.parse(JSON.stringify(o, (k, v) => {
        if (typeof v === 'function') return '[function]';
        if (v instanceof HTMLElement) return '[HTMLElement]';
        return v;
      }));
    } catch (e) { return '[no serializable: ' + e.message + ']'; }
  };
  const t = (x) => typeof x;
  return {
    dataLayer: Array.isArray(window.dataLayer)
      ? window.dataLayer.slice(0, 100).map(safeItem)
      : (window.dataLayer === undefined ? null : '[definido pero no es un array]'),
    dataLayer_length: Array.isArray(window.dataLayer) ? window.dataLayer.length : 0,
    globals: {
      gtag: t(window.gtag),
      ga: t(window.ga),
      fbq: t(window.fbq),
      ttq: t(window.ttq),
      lintrk: t(window.lintrk),
      uetq: window.uetq !== undefined,
      hj: t(window.hj),
      clarity: t(window.clarity),
      _hsq: window._hsq !== undefined,
      twq: t(window.twq),
      pintrk: t(window.pintrk),
      snaptr: t(window.snaptr),
      _linkedin_data_partner_ids: window._linkedin_data_partner_ids || null,
      google_tag_manager: window.google_tag_manager
        ? Object.keys(window.google_tag_manager).filter(k => /^(GTM|G|AW|DC)-/.test(k))
        : null,
      google_tag_data_present: window.google_tag_data !== undefined,
      __tcfapi: t(window.__tcfapi),
      OneTrust: t(window.OneTrust),
      Cookiebot: t(window.Cookiebot),
      Didomi: t(window.Didomi),
      UC_UI: t(window.UC_UI),
      Osano: t(window.Osano),
      axeptio: t(window.axeptio),
      _iub: window._iub !== undefined,
      complianz: window.complianz !== undefined,
    },
  };
}
"""


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def try_fill_and_submit(page, phase, email, name, phone):
    """Localiza el formulario principal, lo rellena con datos de prueba y lo
    envía. Devuelve un informe de lo que se hizo (prueba de lead)."""
    info = {"form_found": False, "fields": [], "submitted": False,
            "submit_via": None, "url_after": None, "error": None}
    try:
        forms = page.locator("form")
        target, fallback = None, None
        for i in range(min(forms.count(), 15)):
            f = forms.nth(i)
            try:
                if not f.is_visible():
                    continue
                if f.locator("input[type='email']:visible, input[name*='mail' i]:visible, "
                             "input[id*='mail' i]:visible").count() > 0:
                    target = f
                    break
                if fallback is None and \
                        f.locator("input[type='text']:visible, "
                                  "input[type='tel']:visible").count() > 0:
                    fallback = f
            except Exception:
                continue
        target = target or fallback
        if target is None:
            return info
        info["form_found"] = True

        inputs = target.locator("input:visible, textarea:visible, select:visible")
        for i in range(min(inputs.count(), 25)):
            el = inputs.nth(i)
            try:
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                typ = (el.get_attribute("type") or "text").lower()
                meta = " ".join(filter(None, [
                    el.get_attribute("name"), el.get_attribute("id"),
                    el.get_attribute("placeholder"),
                    el.get_attribute("autocomplete")])).lower()
                label = (meta[:60] or typ or tag)
                if tag == "select":
                    try:
                        el.select_option(index=1)
                        info["fields"].append({"campo": label, "valor": "(opción 1)"})
                    except Exception:
                        pass
                elif typ == "checkbox":
                    el.check(timeout=1000)
                    info["fields"].append({"campo": label, "valor": "✓"})
                elif typ == "radio":
                    el.check(timeout=1000)
                elif typ == "email" or "mail" in meta:
                    el.fill(email)
                    info["fields"].append({"campo": label, "valor": email})
                elif typ == "tel" or any(k in meta for k in
                                         ("phone", "tel", "movil", "móvil")):
                    el.fill(phone)
                    info["fields"].append({"campo": label, "valor": phone})
                elif tag == "textarea":
                    el.fill("Mensaje de prueba — auditoría de medición (Pixel Doctor)")
                    info["fields"].append({"campo": label, "valor": "(mensaje de prueba)"})
                elif typ in ("text", "search"):
                    if any(k in meta for k in ("apellido", "last", "surname")):
                        val = name.split()[-1]
                    elif any(k in meta for k in ("empresa", "company", "organiz")):
                        val = "Empresa Test"
                    else:
                        val = name
                    el.fill(val)
                    info["fields"].append({"campo": label, "valor": val})
                elif typ == "number":
                    el.fill("1")
                    info["fields"].append({"campo": label, "valor": "1"})
            except Exception:
                continue

        # A partir de aquí, todo hit de red cuenta como "post_submit"
        phase["value"] = "post_submit"

        def _nav_exc(e):
            # Si la página navegó/recargó durante el clic, el envío FUNCIONÓ:
            # la navegación es consecuencia del submit, no un fallo.
            s = str(e)
            return ("Execution context was destroyed" in s
                    or "context or browser has been closed" in s
                    or "frame was detached" in s
                    or "Navigation" in s)

        try:
            target.locator("button[type='submit'], input[type='submit']").first \
                  .click(timeout=4000)
            info["submitted"] = True
            info["submit_via"] = "botón submit"
        except Exception as e1:
            if _nav_exc(e1):
                info["submitted"] = True
                info["submit_via"] = "botón submit (la página navegó al enviar)"
            else:
                try:
                    target.locator("button").first.click(timeout=2000)
                    info["submitted"] = True
                    info["submit_via"] = "primer botón del formulario"
                except Exception as e2:
                    if _nav_exc(e2):
                        info["submitted"] = True
                        info["submit_via"] = "botón (la página navegó al enviar)"
                    else:
                        try:
                            target.evaluate(
                                "f => f.requestSubmit ? f.requestSubmit() : f.submit()")
                            info["submitted"] = True
                            info["submit_via"] = "form.submit()"
                        except Exception as e3:
                            if _nav_exc(e3):
                                info["submitted"] = True
                                info["submit_via"] = "form.submit() (con navegación)"
                            else:
                                info["error"] = f"No se pudo enviar: {str(e3)[:200]}"

        if info["submitted"]:
            page.wait_for_timeout(6000)
            try:
                page.wait_for_load_state("networkidle", timeout=6000)
            except PWTimeout:
                pass
            info["url_after"] = page.url
    except Exception as e:
        info["error"] = str(e)[:300]
    return info


def scan(url, wait_ms=6000, consent=False, interact=False, mobile=False,
         timeout_ms=45000, submit_form=False, test_email="test@ejemplo.com",
         test_name="Prueba Medicion", test_phone="+34600000000"):
    result = {
        "url": url,
        "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "options": {"consent": consent, "interact": interact, "mobile": mobile,
                    "wait_ms": wait_ms, "submit_form": submit_form,
                    "test_email": test_email if submit_form else None},
        "lead_test": None,
        "js_pre_submit": None,
        "requests": [],
        "console": [],
        "page_errors": [],
        "navigation": {},
        "consent_click": None,
        "js_pre_consent": None,
        "js": None,
        "cookies_pre_consent": [],
        "cookies": [],
        "html": "",
        "error": None,
    }

    phase = {"value": "pre_consent" if consent else "load"}
    t0 = time.monotonic()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            user_agent=MOBILE_UA if mobile else DESKTOP_UA,
            viewport={"width": 390, "height": 844} if mobile else {"width": 1440, "height": 900},
            locale="es-ES",
            timezone_id="Europe/Madrid",
        )
        # Reducir señales de automatización (algunos anti-bot bloquean headless)
        try:
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        except Exception:
            pass
        page = context.new_page()

        req_index = {}

        def on_request(req):
            if len(result["requests"]) >= MAX_REQUESTS:
                return
            post = None
            try:
                post = req.post_data
                if post and len(post) > MAX_POST_DATA:
                    post = post[:MAX_POST_DATA]
            except Exception:
                post = None
            entry = {
                "url": req.url[:2500],
                "method": req.method,
                "resource_type": req.resource_type,
                "post_data": post,
                "status": None,
                "failure": None,
                "phase": phase["value"],
                "ts": round(time.monotonic() - t0, 2),
            }
            result["requests"].append(entry)
            req_index[req] = entry

        def on_response(resp):
            entry = req_index.get(resp.request)
            if entry:
                entry["status"] = resp.status

        def on_requestfailed(req):
            entry = req_index.get(req)
            if entry:
                entry["failure"] = req.failure

        def on_console(msg):
            if len(result["console"]) < MAX_CONSOLE:
                result["console"].append({"type": msg.type, "text": msg.text[:1000]})

        page.on("request", on_request)
        page.on("response", on_response)
        page.on("requestfailed", on_requestfailed)
        page.on("console", on_console)
        page.on("pageerror", lambda err: result["page_errors"].append(str(err)[:1000])
                if len(result["page_errors"]) < 50 else None)

        try:
            resp = page.goto(url, wait_until="load", timeout=timeout_ms)
            result["navigation"] = {
                "status": resp.status if resp else None,
                "final_url": page.url,
                "redirected": (page.url.rstrip("/") != url.rstrip("/")),
            }
        except PWTimeout:
            result["navigation"] = {"status": None, "final_url": page.url,
                                    "timeout": True}
            log(f"[aviso] Timeout cargando {url}; se continúa con lo capturado")
        except Exception as e:
            result["error"] = f"No se pudo cargar la página: {e}"
            browser.close()
            return result

        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PWTimeout:
            pass
        page.wait_for_timeout(wait_ms)

        if interact:
            try:
                page.mouse.wheel(0, 1200)
                page.wait_for_timeout(1200)
                page.mouse.wheel(0, 2400)
                page.wait_for_timeout(1500)
            except Exception:
                pass

        # Snapshot antes del consentimiento
        try:
            result["js_pre_consent"] = page.evaluate(JS_SNAPSHOT)
        except Exception as e:
            log(f"[aviso] snapshot JS pre-consent falló: {e}")
        try:
            result["cookies_pre_consent"] = [
                {"name": c["name"], "domain": c["domain"], "value": (c.get("value") or "")[:180]}
                for c in context.cookies()
            ]
        except Exception:
            pass

        if consent:
            clicked = None
            for sel in CONSENT_SELECTORS:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=400):
                        phase["value"] = "post_consent"
                        el.click(timeout=3000)
                        clicked = sel
                        break
                except Exception:
                    continue
            if not clicked:
                try:
                    el = page.get_by_role("button", name=re.compile(CONSENT_TEXT_RE)).first
                    if el.is_visible(timeout=400):
                        phase["value"] = "post_consent"
                        el.click(timeout=3000)
                        clicked = "texto: botón aceptar"
                except Exception:
                    pass
            if not clicked:
                # Respaldo: API JS del CMP (funciona aunque el banner no se vea)
                try:
                    phase["value"] = "post_consent"
                    api = page.evaluate(CONSENT_JS_APIS)
                except Exception:
                    api = None
                if api:
                    clicked = api
            result["consent_click"] = clicked
            if clicked:
                log(f"[info] Consentimiento aceptado vía {clicked}")
                page.wait_for_timeout(5000)
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except PWTimeout:
                    pass
            else:
                log("[info] No se encontró banner de consentimiento que aceptar")
                phase["value"] = "post_consent"  # no había banner: todo cuenta igual

        if submit_form:
            try:
                result["js_pre_submit"] = page.evaluate(JS_SNAPSHOT)
            except Exception:
                pass
            log("[info] Prueba de lead: buscando y enviando el formulario…")
            result["lead_test"] = try_fill_and_submit(
                page, phase, test_email, test_name, test_phone)
            lt = result["lead_test"]
            if lt["form_found"] and not lt["submitted"]:
                # Respaldo por evidencia de red: si tras el intento salió un POST
                # que no es de tracking, el formulario SÍ se envió aunque el clic
                # reportara error (típico cuando la página recarga al enviar).
                page.wait_for_timeout(3000)
                _tracking_hints = ("google", "facebook", "doubleclick", "licdn",
                                   "linkedin", "tiktok", "bing", "clarity",
                                   "hotjar", "googlesyndication")
                posts = [r for r in result["requests"]
                         if r.get("phase") == "post_submit"
                         and r["method"] == "POST"
                         and not any(h in r["url"] for h in _tracking_hints)]
                if posts:
                    lt["submitted"] = True
                    lt["submit_via"] = "envío confirmado por red (POST del formulario)"
                    lt["error"] = None
                    try:
                        lt["url_after"] = page.url
                    except Exception:
                        pass
            log(f"[info] Prueba de lead: form={lt['form_found']} "
                f"enviado={lt['submitted']} campos={len(lt['fields'])}")

        try:
            result["js"] = page.evaluate(JS_SNAPSHOT)
        except Exception as e:
            log(f"[aviso] snapshot JS final falló: {e}")
        try:
            result["cookies"] = [
                {"name": c["name"], "domain": c["domain"], "value": (c.get("value") or "")[:180]}
                for c in context.cookies()
            ]
        except Exception:
            pass
        try:
            result["html"] = page.content()[:MAX_HTML]
        except Exception:
            pass

        # ¿El sitio sirvió una página de verificación anti-bot en vez del
        # contenido real? Si es así, los resultados NO son representativos.
        try:
            title = (page.title() or "").lower()
        except Exception:
            title = ""
        html_low = (result["html"] or "").lower()
        block_title = any(s in title for s in (
            "just a moment", "attention required", "access denied",
            "un momento", "verificando", "verification required", "bot verification"))
        block_html = any(s in html_low for s in (
            "cf-chl", "challenge-platform", "cf_chl_opt", "turnstile",
            "captcha-delivery.com", "geo.captcha-delivery", "datadome",
            "px-captcha", "perimeterx", "_incapsula_", "imperva",
            "verify you are human", "confirme que es una persona"))
        status_block = (result.get("navigation", {}).get("status") or 0) in (403, 429, 503)
        result["bot_blocked"] = bool(block_title or block_html or
                                     (status_block and len(html_low) < 30000))
        if result["bot_blocked"]:
            log("[aviso] El sitio parece haber servido una página anti-bot: "
                "los resultados no son representativos")

        browser.close()

    return result


def main():
    ap = argparse.ArgumentParser(description="Escáner de píxeles y analítica")
    ap.add_argument("url")
    ap.add_argument("--wait", type=int, default=6000, help="ms extra de espera tras cargar")
    ap.add_argument("--timeout", type=int, default=45000, help="timeout de navegación en ms")
    ap.add_argument("--consent", action="store_true", help="aceptar el banner de cookies")
    ap.add_argument("--interact", action="store_true", help="hacer scroll para disparar tags de scroll")
    ap.add_argument("--mobile", action="store_true", help="emular móvil")
    ap.add_argument("--attribution", nargs="?", const="all", default=None,
                    help="simular llegada de campaña. Sin valor = todas las plataformas; "
                         "o lista separada por comas: google,meta,linkedin,tiktok,bing")
    ap.add_argument("--submit-form", action="store_true",
                    help="prueba de lead: rellenar y ENVIAR el formulario (crea un lead real)")
    ap.add_argument("--test-email", default="test@ejemplo.com")
    ap.add_argument("--test-name", default="Prueba Medicion")
    ap.add_argument("--test-phone", default="+34600000000")
    ap.add_argument("--json", default="-", help="ruta de salida JSON ('-' = stdout)")
    args = ap.parse_args()

    url = args.url
    if not re.match(r"^https?://", url):
        url = "https://" + url
    if args.attribution:
        platforms = (list(ATTRIBUTION_SETS) if args.attribution == "all"
                     else [p.strip().lower() for p in args.attribution.split(",")])
        url = add_attribution_params(url, platforms)
        log(f"[info] URL con parámetros de campaña ({', '.join(platforms)}): {url}")

    data = scan(url, wait_ms=args.wait, consent=args.consent,
                interact=args.interact, mobile=args.mobile,
                timeout_ms=args.timeout, submit_form=args.submit_form,
                test_email=args.test_email, test_name=args.test_name,
                test_phone=args.test_phone)

    out = json.dumps(data, ensure_ascii=False)
    if args.json == "-":
        print(out)
    else:
        with open(args.json, "w") as f:
            f.write(out)
        log(f"[ok] Resultado guardado en {args.json}")


if __name__ == "__main__":
    main()
