"""Motor de diagnóstico.

Aplica reglas sobre el resultado del escaneo + detecciones y produce una
lista de problemas, cada uno con severidad, evidencia y pasos de solución.

Severidades: critico > alto > medio > bajo > info
"""

from .platforms import detect_cmp, tracking_failures

SEVERITY_ORDER = {"critico": 0, "alto": 1, "medio": 2, "bajo": 3, "info": 4}
SEVERITY_WEIGHT = {"critico": 25, "alto": 15, "medio": 8, "bajo": 3, "info": 0}
SEVERITY_LABEL = {
    "critico": "🔴 Crítico", "alto": "🟠 Alto", "medio": "🟡 Medio",
    "bajo": "🔵 Bajo", "info": "⚪ Info",
}


def issue(sev, platform, title, description, fix_steps, evidence=""):
    return {"severity": sev, "platform": platform, "title": title,
            "description": description, "fix_steps": fix_steps,
            "evidence": evidence}


def _events(det, key, phase=None):
    evs = det[key]["events"]
    if phase:
        evs = [e for e in evs if e.get("phase") == phase]
    return evs


def _consent_denied_ga(evs):
    """gcs=G1xx: 2º dígito=ads, 3º=analytics. '0' = denegado."""
    denied = [e for e in evs if e["params"].get("gcs", "").startswith("G1")
              and len(e["params"].get("gcs", "")) >= 4
              and e["params"]["gcs"][3] == "0"]
    return denied


def run_rules(scan, det):
    issues = []
    cmp_info = detect_cmp(scan)
    consent_mode = scan.get("options", {}).get("consent", False)
    js = scan.get("js") or {}
    g = js.get("globals") or {}
    html = scan.get("html", "")

    gtm, ga4, gads, ua = det["gtm"], det["ga4"], det["gads"], det["ua"]
    meta, li, tt, bing = det["meta"], det["linkedin"], det["tiktok"], det["bing"]

    # ---------------------------------------------------------- Globales --
    nav = scan.get("navigation", {})
    if scan.get("error"):
        issues.append(issue(
            "critico", "general", "La página no se pudo cargar",
            f"El escáner no pudo cargar la URL: {scan['error']}",
            ["Verifica que la URL es correcta y accesible públicamente.",
             "Si la página requiere login o VPN, el escáner no puede acceder.",
             "Prueba a abrirla en una ventana de incógnito de tu navegador."]))
        return issues, cmp_info

    if nav.get("status") and nav["status"] >= 400:
        issues.append(issue(
            "critico", "general", f"La página devuelve HTTP {nav['status']}",
            "La URL escaneada responde con un código de error, por lo que "
            "ninguna medición puede funcionar en ella.",
            ["Corrige el error del servidor o la URL (¿redirección rota, página eliminada?).",
             "Si la landing cambió de URL, actualiza las URLs finales de tus campañas."],
            f"Estado HTTP: {nav['status']} en {nav.get('final_url')}"))

    any_tag = any(det[k]["detected"] for k in det)
    if not any_tag:
        issues.append(issue(
            "critico", "general", "No se detectó NINGÚN sistema de medición",
            "La página no tiene Google Tag Manager, Analytics, ni ningún píxel "
            "publicitario. Todo el tráfico y las conversiones se están perdiendo.",
            ["Crea un contenedor en tagmanager.google.com y copia los 2 fragmentos "
             "de código (uno en <head> y otro tras <body>).",
             "Publica el contenedor y añade dentro las etiquetas de GA4 y de tus "
             "plataformas de ads (Google Ads, Meta, LinkedIn, TikTok, Bing).",
             "Vuelve a escanear esta página para confirmar que todo dispara."]))
        return issues, cmp_info

    # -------------------------------------------------------------- GTM --
    if gtm["in_html"] and not gtm["library_loaded"]:
        failures = [r for r in scan["requests"]
                    if "googletagmanager.com/gtm.js" in r["url"] and
                    (r.get("failure") or (r.get("status") or 0) >= 400)]
        ev = failures[0]["url"] + " → " + str(failures[0].get("failure") or failures[0].get("status")) if failures else \
            "El ID GTM- aparece en el HTML pero gtm.js nunca se descargó."
        issues.append(issue(
            "critico", "gtm", "GTM está en el código pero NO se carga",
            "El snippet de Google Tag Manager está en la página pero el script "
            "gtm.js no llega a descargarse: ninguna etiqueta del contenedor "
            "puede funcionar.",
            ["Abre la página con DevTools (F12) → pestaña Red → filtra por 'gtm.js' "
             "y mira si aparece en rojo (bloqueado) o con error.",
             "Causas típicas: el snippet está mal copiado o comentado; una política CSP "
             "del servidor bloquea googletagmanager.com (revisa la consola); el CMP "
             "bloquea GTM entero hasta el consentimiento (mala práctica: GTM debe "
             "cargar siempre y gestionar el consentimiento con Consent Mode).",
             "Si usas un CMP, configura googletagmanager.com como 'necesario/exento' "
             "y controla las etiquetas individuales con Consent Mode.",
             "Corrige y vuelve a escanear."], ev))

    if len(gtm["containers_loaded"]) > 1:
        issues.append(issue(
            "medio", "gtm", f"Hay {len(gtm['containers_loaded'])} contenedores GTM cargándose",
            "Cargar varios contenedores suele provocar etiquetas y eventos duplicados "
            "(dobles page_view, conversiones contadas dos veces).",
            [f"Contenedores detectados: {', '.join(gtm['containers_loaded'])}.",
             "Decide cuál es el contenedor 'bueno' (mira en tagmanager.google.com "
             "cuál tiene las etiquetas activas y publicadas).",
             "Elimina el snippet del contenedor sobrante del código de la página "
             "o del gestor de la plantilla (WordPress, Shopify, etc.).",
             "Verifica después que no se han duplicado los page_view de GA4."]))

    if gtm["library_loaded"]:
        core_hits = sum(len(_events(det, k)) for k in ("ga4", "gads", "meta", "linkedin", "tiktok", "bing"))
        if core_hits == 0:
            issues.append(issue(
                "alto", "gtm", "GTM carga pero NINGUNA etiqueta dispara",
                "El contenedor se descarga correctamente pero no sale ni un solo hit "
                "hacia Analytics ni hacia plataformas de ads.",
                ["Entra en tagmanager.google.com → tu contenedor → botón 'Vista previa' "
                 "y abre esta misma URL: verás qué etiquetas disparan y cuáles no.",
                 "Si el contenedor está vacío o las etiquetas están pausadas, créalas/actívalas.",
                 "Si las etiquetas existen pero no disparan, revisa sus activadores "
                 "(¿esperan un evento del dataLayer que nunca llega?).",
                 "Si usas Consent Mode, comprueba que el CMP envía la actualización de "
                 "consentimiento; si el usuario acepta y aun así no disparan, la integración "
                 "CMP↔GTM está rota (revisa la plantilla del CMP en GTM).",
                 "Comprueba también que el contenedor está PUBLICADO (no solo guardado)."]))

    # dataLayer
    if gtm["detected"] and js.get("dataLayer") is None:
        issues.append(issue(
            "alto", "gtm", "window.dataLayer no existe",
            "GTM está presente pero el dataLayer no está definido. Los eventos "
            "personalizados (formularios, compras…) no pueden comunicarse con GTM.",
            ["Asegúrate de que `window.dataLayer = window.dataLayer || [];` va ANTES "
             "del snippet de GTM en el <head>.",
             "Si tu web empuja eventos con `dataLayer.push({...})`, deben usar exactamente "
             "el mismo nombre de variable ('dataLayer', salvo que GTM se cargara con otro nombre).",
             "Revisa en la consola del navegador: escribe `dataLayer` y pulsa Enter; "
             "debería mostrar un array."]))
    # (la cobertura de eventos personalizados la evalúa scanner/quality.py)

    # -------------------------------------------------------------- GA4 --
    ga4_evs = _events(det, "ga4")
    if ga4["detected"]:
        if not ga4_evs:
            if ga4["library_loaded"] or ga4["in_html"] or gtm["library_loaded"]:
                issues.append(issue(
                    "alto", "ga4", "GA4 está instalado pero NO envía ningún hit",
                    "La etiqueta de GA4 existe pero no salió ninguna petición a "
                    "google-analytics.com/g/collect. En GA4 esta página aparece "
                    "como si no tuviera visitas.",
                    ["Si usas Consent Mode: sin consentimiento GA4 puede no enviar nada "
                     "(o solo pings sin cookies). Escanea de nuevo con la opción "
                     "'Aceptar consentimiento' activada y compara.",
                     "Abre DevTools → Red → filtra 'collect' y recarga: si no aparece nada, "
                     "la etiqueta no dispara; si aparece en rojo, algo la bloquea (CSP, adblock).",
                     "En GTM: Vista previa → comprueba que la etiqueta 'Google Analytics: "
                     "configuración GA4' dispara en 'Initialization' o 'Page View'.",
                     "Verifica que el ID de medición (G-XXXX) es el correcto: Analytics → "
                     "Administración → Flujos de datos → tu web.",
                     "Comprueba en GA4 el informe 'Tiempo real' mientras navegas tú mismo."]))
        else:
            # page_view duplicado por el mismo ID (solo explícitos y en la misma
            # fase: el reenvío tras aceptar consentimiento es comportamiento normal)
            for tid in ga4["ids"]:
                by_phase = {}
                for e in ga4_evs:
                    if e["id"] == tid and e["event"] == "page_view":
                        by_phase.setdefault(e["phase"], []).append(e)
                pv = max(by_phase.values(), key=len) if by_phase else []
                if len(pv) > 1:
                    issues.append(issue(
                        "alto", "ga4", f"page_view DUPLICADO en {tid} ({len(pv)} envíos)",
                        "La misma propiedad GA4 recibe varios page_view en una sola carga: "
                        "las visitas y tasas de conversión quedan infladas/distorsionadas.",
                        ["Causa típica 1: GA4 instalado dos veces (código gtag manual + etiqueta "
                         "en GTM). Busca 'gtag/js' en el HTML y elimina una de las dos.",
                         "Causa típica 2: dos etiquetas GA4 de configuración en GTM con el mismo ID; "
                         "deja solo una.",
                         "Causa típica 3: el evento page_view se reenvía en un 'virtual pageview' "
                         "sin desactivar el automático.",
                         "Verifica con GA4 → Tiempo real: una recarga = un page_view."],
                        f"{len(pv)} hits page_view para {tid}"))
            denied = _consent_denied_ga(ga4_evs)
            if denied and len(denied) == len([e for e in ga4_evs if "gcs" in e["params"]]):
                post = [e for e in denied if e.get("phase") == "post_consent"]
                sev = "alto" if (consent_mode and scan.get("consent_click") and post) else "medio"
                desc_extra = (" Incluso DESPUÉS de aceptar el banner, los hits siguen "
                              "llegando con el consentimiento denegado: la integración "
                              "CMP → Consent Mode está rota." if sev == "alto" else
                              " Los hits llegan sin consentimiento de analytics (gcs), por lo que "
                              "GA4 los procesa sin cookies (datos modelados, usuarios no identificados).")
                issues.append(issue(
                    sev, "ga4", "Consent Mode está DENEGANDO la medición de Analytics",
                    "Los hits de GA4 se envían con analytics_storage=denied." + desc_extra,
                    ["Comprueba en tu CMP que la categoría 'Analítica/Estadística' existe y "
                     "está mapeada a `analytics_storage`.",
                     "La integración correcta: el CMP debe ejecutar "
                     "`gtag('consent','update',{analytics_storage:'granted', ...})` al aceptar.",
                     "Si usas la plantilla del CMP en GTM (OneTrust, Cookiebot, etc.), "
                     "actualízala y revisa su configuración de Consent Mode.",
                     "Valida con GTM Vista previa → pestaña 'Consent': tras aceptar, "
                     "analytics_storage debe pasar a 'Granted'.",
                     "Mientras tanto GA4 solo recibe pings anónimos (comportamiento esperado "
                     "de Consent Mode avanzado, pero confirma que tras aceptar cambia a granted)."],
                    f"Ej.: gcs={denied[0]['params'].get('gcs')} en {denied[0]['url'][:120]}"))

    if ga4.get("server_side_hosts"):
        issues.append(issue(
            "info", "ga4", "GA4 se envía por GTM server-side (dominio propio)",
            f"Los hits van a {', '.join(ga4['server_side_hosts'])} en lugar de a "
            "google-analytics.com. Es una implementación avanzada (mejor calidad de "
            "dato y resistencia a adblockers), pero revisa que el contenedor server "
            "reenvía correctamente a GA4.",
            ["Comprueba en GA4 → Tiempo real que las visitas llegan.",
             "Si no llegan, revisa el contenedor server en tagmanager.google.com "
             "(cliente GA4 y etiqueta GA4 configurados) y el mapeo del subdominio."]))

    if not ga4["detected"] and any_tag:
        issues.append(issue(
            "medio", "ga4", "No hay Google Analytics 4 en la página",
            "Hay otros píxeles pero no GA4: pierdes la analítica base (tráfico, "
            "fuentes, embudos) y la posibilidad de importar conversiones a Google Ads.",
            ["Crea una propiedad GA4 en analytics.google.com si no la tienes.",
             "Añade la etiqueta 'Google Analytics GA4' en GTM con tu ID G-XXXX, "
             "activador 'Initialization - All Pages'.",
             "Publica el contenedor y verifica en Tiempo real."]))

    # --------------------------------------------------------------- UA --
    if ua["detected"]:
        issues.append(issue(
            "medio", "ua", "Universal Analytics (UA-) sigue instalado — está MUERTO",
            "UA dejó de procesar datos el 1-jul-2023. Este código ya no mide nada, "
            "ensucia el rendimiento y puede confundir a quien mantenga la web.",
            [f"IDs detectados: {', '.join(ua['ids']) or '(en HTML)'}.",
             "Elimina el snippet analytics.js/ga.js del código o la etiqueta UA de GTM.",
             "Confirma que existe una etiqueta GA4 equivalente antes de borrar."]))

    # ------------------------------------------------------- Google Ads --
    gads_evs = _events(det, "gads")
    if gads["detected"]:
        has_gcl = any(c["name"].startswith("_gcl") for c in scan.get("cookies", []))
        if not has_gcl:
            issues.append(issue(
                "alto", "gads", "Falta la cookie _gcl_* (Conversion Linker)",
                "El tag de Google Ads está presente pero no se crea la cookie _gcl_au/_gcl_aw. "
                "Sin ella, los clics (GCLID) no se asocian a las conversiones y Google Ads "
                "atribuirá mal o perderá conversiones.",
                ["Si la causa es el consentimiento: sin `ad_storage=granted` la cookie no se "
                 "crea (correcto legalmente). Escanea con 'Aceptar consentimiento' y verifica "
                 "que entonces SÍ se crea.",
                 "En GTM añade la etiqueta 'Conversion Linker' con activador All Pages si no existe.",
                 "Comprueba que el auto-etiquetado (GCLID) está activo en Google Ads → "
                 "Configuración de la cuenta.",
                 "Si la landing pasa por redirecciones, verifica que el parámetro gclid "
                 "sobrevive hasta esta página (míralo en la URL final)."]))
        conv = [e for e in gads_evs if e["event"].startswith("conversión")]
        if not conv:
            issues.append(issue(
                "info", "gads", "No se disparó ninguna conversión de Google Ads al cargar",
                "Es lo esperado si la conversión se dispara en interacciones (formulario, "
                "compra, página de gracias). Este escaneo solo carga la página.",
                ["Para probar la conversión: navega tú mismo hasta completar la acción "
                 "(o escanea directamente la página de gracias).",
                 "En Google Ads → Objetivos → Conversiones, revisa la columna 'Estado' "
                 "de cada acción: 'Sin conversiones recientes' o 'Inactiva' indican problema.",
                 "Con GTM Vista previa, completa el formulario en modo test y verifica que "
                 "la etiqueta de conversión dispara con el label correcto."]))
    elif ga4["detected"]:
        issues.append(issue(
            "bajo", "gads", "No hay tag de Google Ads (AW-)",
            "Si haces campañas de Google Ads, no hay remarketing ni conversiones "
            "medidas por tag en esta página.",
            ["Si mides conversiones importándolas desde GA4, es válido — pero pierdes "
             "remarketing y las conversiones mejoradas del tag.",
             "Para instalarlo: Google Ads → Herramientas → Conversiones → configura la "
             "etiqueta AW- vía GTM (etiqueta 'Conversión de Google Ads' + 'Conversion Linker')."]))

    # ------------------------------------------------------------- Meta --
    meta_evs = _events(det, "meta")
    if meta["detected"]:
        if meta["library_loaded"] and not meta_evs:
            issues.append(issue(
                "alto", "meta", "Meta Pixel carga pero NO envía eventos (ni PageView)",
                "fbevents.js se descarga pero no sale ninguna petición a facebook.com/tr. "
                "Meta no recibe ni el PageView: las campañas no optimizan ni atribuyen.",
                ["Causa 1 — falta el init: busca en el código `fbq('init','TU_PIXEL_ID')` "
                 "seguido de `fbq('track','PageView')`. Sin init no se envía nada.",
                 "Causa 2 — consentimiento: si el CMP bloquea el píxel hasta aceptar, escanea "
                 "con 'Aceptar consentimiento' y comprueba si entonces dispara.",
                 "Causa 3 — bloqueo de red: DevTools → Red → filtra 'facebook' y mira errores.",
                 "Verifica con la extensión 'Meta Pixel Helper' de Chrome y con "
                 "Meta Events Manager → Probar eventos (introduce esta URL)."]))
        for pid in meta["ids"]:
            pv = [e for e in meta_evs if e["id"] == pid and e["event"] == "PageView"]
            if len(pv) > 1:
                issues.append(issue(
                    "alto", "meta", f"PageView DUPLICADO del píxel {pid} ({len(pv)} envíos)",
                    "El mismo píxel envía PageView varias veces por carga: infla métricas "
                    "y rompe la optimización de campañas.",
                    ["Casi siempre es doble instalación: píxel hardcodeado en el tema/plugin "
                     "(WordPress, Shopify, Elementor…) Y además en GTM. Deja solo una.",
                     "Busca 'fbevents.js' y `fbq('init'` en el código fuente de la página "
                     "para localizar la copia manual.",
                     "Verifica con Meta Pixel Helper: debe mostrar 1 solo PageView."],
                    f"{len(pv)} PageView del píxel {pid}"))
        if len(meta["ids"]) > 1:
            issues.append(issue(
                "medio", "meta", f"Hay {len(meta['ids'])} píxeles de Meta distintos",
                f"IDs: {', '.join(meta['ids'])}. Puede ser intencionado (agencia + cliente), "
                "pero a menudo es un píxel antiguo olvidado.",
                ["Comprueba en Meta Events Manager a qué cuenta pertenece cada ID.",
                 "Elimina el que no corresponda (código o GTM)."]))
        no_eid = [e for e in meta_evs if e["event"] not in ("(sin ev)",) and "eid" not in e["params"]]
        if meta_evs and len(no_eid) == len(meta_evs):
            issues.append(issue(
                "info", "meta", "Los eventos del píxel no llevan event_id (deduplicación CAPI)",
                "Si usas o piensas usar la API de Conversiones (CAPI), sin event_id los "
                "eventos se contarán DOS veces (píxel + servidor).",
                ["Si NO usas CAPI, ignora este aviso.",
                 "Si usas CAPI: genera un event_id único por evento y envíalo en ambos "
                 "canales (píxel: `fbq('track','Lead',{},{eventID:'...'})` y en el payload CAPI).",
                 "Verifica la deduplicación en Events Manager → tu píxel → un evento → "
                 "'Deduplicación'."]))

    # --------------------------------------------------------- LinkedIn --
    li_evs = _events(det, "linkedin")
    if li["detected"]:
        if (li["library_loaded"] or li["in_html"]) and not li_evs:
            issues.append(issue(
                "alto", "linkedin", "LinkedIn Insight Tag no envía datos",
                "El script insight.min.js está en la página pero no sale ninguna "
                "petición a px.ads.linkedin.com: LinkedIn no registra la visita.",
                ["Comprueba que `_linkedin_partner_id` está definido ANTES de cargar el script.",
                 "Si va por GTM, usa la plantilla oficial 'LinkedIn Insight Tag 2.0' con tu "
                 "Partner ID y activador All Pages.",
                 "Consentimiento: escanea con 'Aceptar consentimiento' y compara.",
                 "Valida en LinkedIn Campaign Manager → Analizar → Insight Tag: debe poner "
                 "'Activo' con señales recientes de este dominio."]))
        if li["in_html"] and not li["ids"]:
            issues.append(issue(
                "critico", "linkedin", "Insight Tag sin Partner ID",
                "El código del Insight Tag está pero no se encontró ningún Partner ID: "
                "no puede asociar los datos a tu cuenta.",
                ["Copia el Partner ID desde Campaign Manager → Analizar → Insight Tag.",
                 "Define `_linkedin_partner_id = 'TU_ID';` antes del script del tag."]))

    # ----------------------------------------------------------- TikTok --
    tt_evs = _events(det, "tiktok")
    if tt["detected"] and (tt["library_loaded"] or tt["in_html"]) and not tt_evs:
        issues.append(issue(
            "alto", "tiktok", "TikTok Pixel carga pero no envía eventos",
            "La librería del píxel se descarga pero no hay peticiones de track: "
            "TikTok no recibe ni el Pageview.",
            ["Comprueba que existe `ttq.load('PIXEL_ID')` y `ttq.page()` en el código "
             "(o que la etiqueta de GTM está bien configurada con el ID).",
             "Consentimiento: escanea con 'Aceptar consentimiento' y compara.",
             "Valida en TikTok Ads Manager → Assets → Events → Web Events → tu píxel → "
             "'Test Events' con esta URL."]))

    # ------------------------------------------------------------- Bing --
    bing_evs = _events(det, "bing")
    if bing["detected"] and (bing["library_loaded"] or bing["in_html"]) and not bing_evs:
        issues.append(issue(
            "alto", "bing", "UET de Microsoft Ads carga pero no envía el pageLoad",
            "bat.js está presente pero no sale la petición a bat.bing.com/action: "
            "Microsoft Ads no registra visitas ni conversiones.",
            ["Comprueba que el snippet incluye tu TI (tag ID) correcto: "
             "Microsoft Ads → Herramientas → Etiquetas UET.",
             "Si va por GTM, revisa la etiqueta UET y su activador All Pages.",
             "Consentimiento: escanea con 'Aceptar consentimiento' y compara.",
             "Usa la extensión 'UET Tag Helper' de Microsoft para validar."]))

    # ------------------------------------------- Bloqueos / errores red --
    fails = tracking_failures(scan)
    if fails:
        sample = "; ".join(f"{f['url'][:90]} → {f['problema']}" for f in fails[:4])
        issues.append(issue(
            "alto", "general", f"{len(fails)} peticiones de tracking bloqueadas o con error",
            "Hay hits de medición que fallan a nivel de red. Según la causa, pierdes "
            "una parte o la totalidad de los datos de esas plataformas.",
            ["Revisa la lista completa en la pestaña 'Red (hits)'.",
             "net::ERR_BLOCKED_BY_CLIENT en tu navegador = adblock (normal); si ocurre "
             "en este escáner (sin adblock), suele ser CSP o el propio CMP.",
             "Si es CSP: añade los dominios de tracking a la cabecera Content-Security-Policy "
             "(script-src/connect-src/img-src).",
             "HTTP 4xx/5xx: el ID del píxel puede ser inválido o la plataforma rechaza el hit; "
             "revisa el ID en la plataforma correspondiente."],
            sample))

    csp_errors = [c for c in scan.get("console", [])
                  if "Content Security Policy" in c["text"] or "Refused to load" in c["text"]]
    if csp_errors:
        issues.append(issue(
            "alto", "general", "La CSP del sitio está bloqueando scripts",
            "La consola muestra bloqueos por Content-Security-Policy que pueden "
            "impedir cargar scripts de medición.",
            ["Identifica el dominio bloqueado en el mensaje de consola (pestaña 'Consola').",
             "Añade ese dominio a la directiva correspondiente de la cabecera CSP del servidor "
             "(p. ej. script-src https://www.googletagmanager.com https://connect.facebook.net).",
             "Redeploy y vuelve a escanear."],
            csp_errors[0]["text"][:200]))

    js_errors = scan.get("page_errors", [])
    if js_errors:
        issues.append(issue(
            "medio", "general", f"{len(js_errors)} errores JavaScript en la página",
            "Los errores JS pueden interrumpir la ejecución de los scripts de medición "
            "que van después (especialmente si el error ocurre en el <head>).",
            ["Revisa los errores en la pestaña 'Consola' y corrígelos o reporta al desarrollador.",
             "Comprueba si el error ocurre antes de los snippets de medición (si los tags "
             "disparan igualmente, la medición no está afectada)."],
            js_errors[0][:200]))

    # ------------------------------------------------- Consentimiento ----
    pre_cookie_names = {c["name"] for c in scan.get("cookies_pre_consent", [])}
    tracking_cookies_pre = pre_cookie_names & {"_ga", "_gid", "_fbp", "_fbc", "_gcl_au",
                                               "_ttp", "_uetsid", "_uetvid", "li_fat_id",
                                               "hubspotutk", "_hjSessionUser"}
    if cmp_info["cmps"]:
        if consent_mode and tracking_cookies_pre and scan.get("consent_click"):
            issues.append(issue(
                "medio", "consentimiento",
                "Se crean cookies de tracking ANTES de aceptar el banner",
                f"Con el banner aún visible ya existían: {', '.join(sorted(tracking_cookies_pre))}. "
                "Riesgo RGPD: los tags disparan sin esperar al consentimiento.",
                ["Configura Consent Mode con estado por defecto 'denied' ANTES de que "
                 "cargue cualquier tag (gtag('consent','default',{...}) en el <head>).",
                 "En GTM, añade 'Requerir consentimiento adicional' a las etiquetas o usa "
                 "la plantilla del CMP para bloquearlas hasta el update.",
                 "Los píxeles hardcodeados (fuera de GTM) NO obedecen al CMP: muévelos a GTM.",
                 "Vuelve a escanear y comprueba que estas cookies solo aparecen tras aceptar."]))
    else:
        if tracking_cookies_pre or scan.get("cookies"):
            issues.append(issue(
                "medio", "consentimiento", "No se detectó banner de consentimiento (CMP)",
                "La página crea cookies de tracking sin ningún CMP visible. Si tienes "
                "tráfico de la UE/España, es un riesgo de cumplimiento RGPD y además "
                "Google exige un CMP certificado (Consent Mode) para remarketing en la UE.",
                ["Instala un CMP certificado por Google (Cookiebot, OneTrust, CookieYes, "
                 "Didomi, Usercentrics…).",
                 "Actívalo con Consent Mode v2 (categorías → ad_storage, ad_user_data, "
                 "ad_personalization, analytics_storage).",
                 "Sin esto, Google Ads limita el remarketing y la medición en la UE "
                 "desde marzo 2024 (Consent Mode v2 obligatorio)."]))

    if consent_mode and cmp_info["cmps"] and not scan.get("consent_click"):
        issues.append(issue(
            "info", "consentimiento",
            f"CMP detectado ({', '.join(cmp_info['cmps'])}) pero el escáner no pudo aceptarlo",
            "Se pidió aceptar el consentimiento pero no se encontró el botón. Los "
            "resultados reflejan el estado SIN consentimiento.",
            ["Puede que el banner no aparezca (consentimiento recordado por geolocalización "
             "del servidor de escaneo, o banner solo para la UE).",
             "Verifica manualmente: abre la página en incógnito, acepta y comprueba con "
             "DevTools que los hits salen tras aceptar."]))

    # ------------------------------------------------- Prueba de lead ----
    lt = scan.get("lead_test")
    if scan.get("options", {}).get("submit_form"):
        dl_pre_len = ((scan.get("js_pre_submit") or {}).get("dataLayer_length")) or 0
        dl_post = (js.get("dataLayer") or []) if isinstance(js.get("dataLayer"), list) else []
        dl_new = [e for e in dl_post[dl_pre_len:] if isinstance(e, dict)]
        dl_new_events = [e.get("event") for e in dl_new
                         if e.get("event") and not str(e.get("event")).startswith("gtm.")]
        submit_hits = [e for k in det for e in det[k]["events"]
                       if e.get("phase") == "post_submit"
                       and not e["event"].startswith(("ping", "config"))]

        if not lt or not lt.get("form_found"):
            issues.append(issue(
                "info", "lead", "Prueba de lead: no se encontró ningún formulario",
                "Se pidió probar el envío de un lead pero el escáner no localizó un "
                "formulario visible en esta página.",
                ["Si el formulario abre en un popup/modal tras un clic, escanea la URL "
                 "donde el formulario esté visible directamente.",
                 "Si es un iframe de terceros (HubSpot, Typeform…), el escáner no puede "
                 "rellenarlo: pruébalo a mano con GTM Vista previa.",
                 "Verifica manualmente: envía el formulario y mira en DevTools → Red "
                 "qué hits salen tras el envío."]))
        elif not lt.get("submitted"):
            issues.append(issue(
                "medio", "lead", "Prueba de lead: no se pudo enviar el formulario",
                f"Se rellenaron {len(lt.get('fields', []))} campos pero el envío falló"
                + (f": {lt['error']}" if lt.get("error") else "."),
                ["Puede haber un CAPTCHA o validación que el escáner no supera (normal).",
                 "Haz la prueba a mano: envía el formulario con datos de test y observa "
                 "en DevTools → Red los hits que salen tras el envío.",
                 "Alternativa: escanea directamente la página de gracias si existe."]))
        else:
            if not dl_new_events and not submit_hits:
                issues.append(issue(
                    "critico", "lead",
                    "El envío del formulario NO genera NINGÚN evento de medición",
                    "Se envió el formulario correctamente pero ni el dataLayer registró "
                    "ningún evento ni salió ningún hit hacia las plataformas: los leads "
                    "NO se están midiendo (ni GA4, ni conversiones de ads).",
                    ["Implementa un push al dataLayer en el éxito del formulario: "
                     "`dataLayer.push({event:'generate_lead', form_id:'...'})` — o usa el "
                     "activador nativo de GTM 'Envío de formulario' si el form es estándar.",
                     "Crea en GTM: etiqueta GA4 evento 'generate_lead' + etiqueta de conversión "
                     "de Google Ads + evento 'Lead' de Meta (y equivalentes en LinkedIn/TikTok/Bing), "
                     "todas con ese activador.",
                     "Si tras enviar rediriges a una página de gracias, otra opción es disparar "
                     "las conversiones allí con un activador de vista de página de esa URL.",
                     "Publica el contenedor y repite esta prueba de lead."],
                    f"URL tras el envío: {lt.get('url_after', '')}"))
            elif dl_new_events and not submit_hits:
                issues.append(issue(
                    "alto", "lead",
                    "El dataLayer registra el envío pero NINGUNA etiqueta lo envía a las plataformas",
                    f"Tras enviar el formulario el dataLayer recibió: {', '.join(map(str, dl_new_events[:6]))}. "
                    "Pero no salió ningún hit de conversión: falta conectar ese evento con "
                    "las etiquetas en GTM.",
                    ["En GTM crea un activador de 'Evento personalizado' con el nombre EXACTO "
                     f"del evento ({dl_new_events[0]}).",
                     "Asócialo a las etiquetas de conversión: GA4 (evento), Google Ads "
                     "(conversión), Meta (Lead), LinkedIn/TikTok/Bing según uses.",
                     "Comprueba en GTM Vista previa que al enviar el formulario las etiquetas "
                     "disparan, y publica el contenedor."]))

        # PII: ¿el email de prueba viaja en claro a las plataformas?
        test_email = scan.get("options", {}).get("test_email") or ""
        if test_email and lt and lt.get("submitted"):
            from urllib.parse import quote
            variants = {test_email, quote(test_email), quote(test_email, safe=""),
                        test_email.replace("@", "%40")}
            leaks = []
            for r in scan.get("requests", []):
                blob = r["url"] + " " + (r.get("post_data") or "")
                if any(v in blob for v in variants):
                    host = r["url"].split("/")[2] if "://" in r["url"] else ""
                    if any(t in r["url"] for t in ("google-analytics", "facebook.com/tr",
                                                   "linkedin", "tiktok", "bat.bing",
                                                   "collect", "pagead")):
                        leaks.append(host)
            if leaks:
                issues.append(issue(
                    "medio", "lead", "El email del lead se envía EN CLARO a plataformas de medición",
                    f"El email de prueba apareció sin cifrar en hits hacia: "
                    f"{', '.join(sorted(set(leaks))[:5])}. GA4 prohíbe enviar PII y puede "
                    "borrar la propiedad; Meta/otros exigen hashearlo (SHA-256).",
                    ["Localiza qué etiqueta añade el email como parámetro y elimínalo o hashéalo.",
                     "Para conversiones mejoradas (Google) y Advanced Matching (Meta), el email "
                     "debe ir con SHA-256, nunca en claro en la URL.",
                     "Revisa también que la URL de la página de gracias no lleve el email como "
                     "parámetro (?email=...), porque GA4 lo capturaría en page_location."]))

    # --------------------------------------------- Atribución / click-IDs
    _attribution_rules(scan, det, issues)

    # ------------------------------------- Calidad del plan de medición --
    from .quality import quality_rules
    q_issues, _ = quality_rules(scan, det)
    issues.extend(q_issues)

    # Comparativa pre/post consentimiento
    if consent_mode and scan.get("consent_click"):
        post_hits = [e for k in det for e in det[k]["events"]
                     if e.get("phase") in ("post_consent", "post_submit")]
        pre_hits = [e for k in det for e in det[k]["events"] if e.get("phase") == "pre_consent"]
        if not post_hits and pre_hits:
            issues.append(issue(
                "alto", "consentimiento",
                "Tras ACEPTAR el consentimiento no se disparó ningún hit nuevo",
                "El usuario acepta las cookies pero las etiquetas no reaccionan: la "
                "integración CMP → GTM/Consent Mode no comunica la aceptación. Estás "
                "perdiendo la medición incluso de los usuarios que consienten.",
                ["En GTM Vista previa, acepta el banner y mira la pestaña 'Consent': "
                 "los estados deben pasar a Granted y debe verse un evento de actualización "
                 "del CMP (p. ej. OneTrustGroupsUpdated, cookie_consent_update…).",
                 "Configura las etiquetas para que disparen con ese evento de actualización "
                 "(o usa 'Activación de consentimiento' de la plantilla del CMP).",
                 "Si el CMP solo actualiza al recargar la página, activa el ajuste de "
                 "'recarga tras aceptar' o dispara los tags con el evento de update."]))

    # Orden y deduplicación final
    issues.sort(key=lambda i: SEVERITY_ORDER.get(i["severity"], 9))
    return issues, cmp_info


def attribution_audit(scan, det):
    """Auditoría de atribución: qué click-IDs venían en la URL, si sobreviven
    a la redirección, si generan su cookie y si viajan en los hits.
    Devuelve una lista de filas (una por parámetro) o [] si no aplica."""
    from urllib.parse import urlparse, parse_qsl
    q0 = dict(parse_qsl(urlparse(scan.get("url", "")).query, keep_blank_values=True))
    ids = {k: v for k, v in q0.items()
           if k in ("gclid", "fbclid", "msclkid", "ttclid", "li_fat_id")
           or k.startswith("utm_")}
    if not ids:
        return []

    final = scan.get("navigation", {}).get("final_url") or ""
    cookies = {c["name"]: c.get("value", "") for c in scan.get("cookies", [])}
    ga4_evs = det["ga4"]["events"]
    meta_evs = det["meta"]["events"]
    bing_evs = det["bing"]["events"]

    def ga4_dl_contains(val):
        return any(val in (e["params"].get("dl") or "") or val in e["url"]
                   for e in ga4_evs)

    rows = []
    for k, v in ids.items():
        row = {"param": k, "valor": v, "en_url_final": v in final,
               "cookie": "", "cookie_ok": None, "en_hits": None, "hits_de": ""}
        if k == "gclid":
            cname = next((n for n in cookies if n.startswith("_gcl_aw")
                          or n.startswith("_gcl_gb")), None)
            row["cookie"] = cname or "_gcl_aw"
            row["cookie_ok"] = bool(cname and v in cookies[cname])
            row["en_hits"] = ga4_dl_contains(v) or any(
                v in e["url"] or v in str(e["params"]) for e in det["gads"]["events"])
            row["hits_de"] = "GA4/Google Ads"
        elif k == "fbclid":
            row["cookie"] = "_fbc"
            row["cookie_ok"] = bool("_fbc" in cookies and v in cookies["_fbc"])
            row["en_hits"] = any(v in (e["params"].get("fbc") or "") or v in e["url"]
                                 for e in meta_evs)
            row["hits_de"] = "Meta (/tr)"
        elif k == "msclkid":
            row["cookie"] = "_uetmsclkid"
            row["cookie_ok"] = bool("_uetmsclkid" in cookies and v in cookies["_uetmsclkid"])
            row["en_hits"] = any(v in e["url"] for e in bing_evs)
            row["hits_de"] = "Bing UET"
        elif k == "ttclid":
            cname = next((n for n in cookies if n in ("ttclid", "_ttclid")), None)
            row["cookie"] = cname or "ttclid"
            row["cookie_ok"] = bool(cname and v in cookies[cname])
            row["en_hits"] = any(v in e["url"] or v in str(e["params"])
                                 for e in det["tiktok"]["events"])
            row["hits_de"] = "TikTok"
        elif k == "li_fat_id":
            row["cookie"] = "li_fat_id"
            row["cookie_ok"] = bool("li_fat_id" in cookies and v in cookies["li_fat_id"])
            row["en_hits"] = any(v in e["url"] for e in det["linkedin"]["events"])
            row["hits_de"] = "LinkedIn"
        elif k.startswith("utm_"):
            row["cookie"] = "(no aplica)"
            row["cookie_ok"] = None
            row["en_hits"] = ga4_dl_contains(v)
            row["hits_de"] = "GA4 (page_location)"
        rows.append(row)
    return rows


def _attribution_rules(scan, det, issues):
    rows = attribution_audit(scan, det)
    if not rows:
        return
    consent_hint = ("Si escaneaste sin la opción 'Aceptar consentimiento', repite "
                    "con ella activada: sin consentimiento estas cookies no deben "
                    "crearse (comportamiento correcto).")

    perdidos = [r["param"] for r in rows if not r["en_url_final"]]
    if perdidos:
        issues.append(issue(
            "alto", "atribucion",
            f"La redirección ELIMINA parámetros de campaña: {', '.join(perdidos)}",
            "La URL final ya no contiene estos parámetros. Todo lo que dependa de "
            "ellos (atribución de Google Ads por GCLID, _fbc de Meta, UTMs en GA4) "
            "se pierde en el camino.",
            [f"URL final: {scan.get('navigation', {}).get('final_url', '')}",
             "Corrige la redirección (http→https, con/sin www, o cambio de ruta) para "
             "que conserve el query string completo.",
             "Si usas un acortador o un enlace intermedio (linktr.ee, etc.), configúralo "
             "para pasar los parámetros.",
             "Mientras tanto, apunta las campañas directamente a la URL final."]))

    for r in rows:
        k = r["param"]
        if k == "gclid" and r["en_url_final"] and r["cookie_ok"] is False:
            plataforma_ok = det["gads"]["detected"] or det["gtm"]["detected"]
            issues.append(issue(
                "alto" if plataforma_ok else "medio", "atribucion",
                "El GCLID llega pero NO se guarda en la cookie _gcl_aw",
                "Sin esa cookie, la conversión no se puede asociar al clic del anuncio: "
                "Google Ads pierde la atribución (especialmente con conversiones que "
                "ocurren en otra página o sesión).",
                ["Añade la etiqueta 'Conversion Linker' en GTM (activador All Pages).",
                 "Comprueba que hay un tag de Google (gtag AW- o GA4) cargando en la página.",
                 "Consent Mode: la cookie solo se crea con ad_storage=granted. " + consent_hint,
                 "Verifica después: DevTools → Application → Cookies → busca _gcl_aw; debe "
                 "contener el gclid."]))
        if k == "fbclid" and r["en_url_final"] and r["cookie_ok"] is False:
            issues.append(issue(
                "alto" if det["meta"]["detected"] else "medio", "atribucion",
                "El fbclid llega pero NO se crea la cookie _fbc",
                "El píxel de Meta debería convertir el fbclid en la cookie _fbc al cargar. "
                "Sin _fbc (y _fbp), Meta pierde capacidad de atribución y el Event Match "
                "Quality de CAPI baja.",
                ["Comprueba que el píxel de Meta carga y dispara PageView en esta página "
                 "(pestaña Plataformas).",
                 "Consent Mode/CMP: si el píxel se bloquea hasta aceptar, la cookie se crea "
                 "tras el consentimiento. " + consent_hint,
                 "Verifica en DevTools → Application → Cookies: _fbc debe contener el fbclid "
                 "y debe existir _fbp.",
                 "Si usas CAPI, envía también fbc/fbp en el payload del servidor."]))
        if k == "li_fat_id" and r["en_url_final"] and r["cookie_ok"] is False \
                and det["linkedin"]["detected"]:
            issues.append(issue(
                "medio", "atribucion",
                "El li_fat_id de LinkedIn llega pero no se guarda en su cookie",
                "El Insight Tag debería guardar el click-ID de LinkedIn en la cookie "
                "li_fat_id. Sin ella, las conversiones pierden la atribución al clic "
                "del anuncio.",
                ["Comprueba que el Insight Tag carga y envía datos en esta página "
                 "(pestaña Plataformas).",
                 "Consentimiento: si el tag se bloquea hasta aceptar, escanea con "
                 "'Aceptar consentimiento' y compara.",
                 "Verifica en DevTools → Application → Cookies que li_fat_id existe "
                 "tras llegar con el parámetro en la URL."]))
        if k == "fbclid" and det["meta"]["events"] and r["en_hits"] is False and r["cookie_ok"]:
            issues.append(issue(
                "medio", "atribucion",
                "La cookie _fbc existe pero los hits del píxel no la envían",
                "Los eventos hacia facebook.com/tr no llevan el parámetro fbc: Meta no "
                "recibe el click-ID aunque esté guardado.",
                ["Suele ocurrir cuando el PageView dispara ANTES de que se procese el "
                 "fbclid: revisa el orden de carga del píxel.",
                 "Verifica con Meta Pixel Helper que el evento incluye fbc.",
                 "Si persiste, envía el evento con un pequeño retardo o vía GTM tras "
                 "el evento de consentimiento."]))

    # _fbp ausente con Meta activo
    cookies = {c["name"] for c in scan.get("cookies", [])}
    if det["meta"]["detected"] and det["meta"]["events"] and "_fbp" not in cookies:
        issues.append(issue(
            "medio", "atribucion", "Falta la cookie _fbp de Meta",
            "El píxel dispara pero no crea _fbp (identificador de navegador). Sin ella "
            "empeora el matching de audiencias y la deduplicación con CAPI.",
            ["Puede deberse al consentimiento (sin ad_storage no se crea — correcto).",
             "Comprueba que el píxel no está en modo 'limited data use' o bloqueado "
             "por la configuración del CMP incluso tras aceptar."]))

    utm_rows = [r for r in rows if r["param"].startswith("utm_")]
    ga4_evs = det["ga4"]["events"]
    if utm_rows and ga4_evs and not any(r["en_hits"] for r in utm_rows):
        issues.append(issue(
            "medio", "atribucion",
            "Las UTM no aparecen en los hits de GA4 (page_location)",
            "GA4 recibe eventos pero el parámetro dl/page_location no contiene las UTM: "
            "las sesiones no se atribuirán a la campaña.",
            ["Causa típica: una redirección o el router de la SPA limpia la URL antes "
             "de que dispare el page_view.",
             "Dispara la etiqueta GA4 en 'Initialization' (antes de que la SPA reescriba "
             "la URL), o conserva el query string en el historial (history.replaceState).",
             "Verifica en GA4 Tiempo real → usuario → fuente/medio de la sesión."]))


# ---------------------------------------------- Conclusión y plan de acción

def _issue_owner(iss):
    """Quién debería aplicar el arreglo, según el tipo de problema."""
    text = (iss["title"] + " " + iss["description"] + " " +
            " ".join(iss["fix_steps"])).lower()
    dev_signals = ("csp", "redirección", "redirige", "servidor", "código fuente",
                   "history.replacestate", "errores javascript", "http", "snippet",
                   "datalayer.push", "<head>", "redeploy")
    gtm_signals = ("gtm", "etiqueta", "activador", "contenedor", "consent mode",
                   "cmp", "conversion linker", "tagmanager", "vista previa",
                   "events manager", "campaign manager", "ads manager")
    is_dev = any(s in text for s in dev_signals)
    is_gtm = any(s in text for s in gtm_signals)
    if is_dev and is_gtm:
        return "Marketing + Desarrollador"
    if is_dev:
        return "Desarrollador web"
    return "Marketing (GTM/plataformas)"


def build_action_plan(issues, score):
    """Conclusión ejecutiva + plan de acción priorizado a partir de los problemas."""
    reales = [i for i in issues if i["severity"] != "info"]
    n_crit = sum(1 for i in reales if i["severity"] == "critico")
    n_alto = sum(1 for i in reales if i["severity"] == "alto")
    n_medio = sum(1 for i in reales if i["severity"] == "medio")

    # Veredicto
    if not reales:
        veredicto = ("La medición de esta página está sana: las plataformas cargan, "
                     "los eventos se envían y no se detectó ningún problema que "
                     "requiera acción. Solo hay observaciones informativas.")
        nivel = "ok"
    else:
        peor = reales[0]  # ya vienen ordenados por severidad
        if n_crit:
            veredicto = (f"La medición tiene {n_crit} problema(s) CRÍTICO(S) que la "
                         f"dejan inservible en parte o del todo. El más grave: "
                         f"«{peor['title']}». Hasta corregirlo, los datos de esta "
                         "página no son fiables y las campañas no pueden optimizar.")
            nivel = "critico"
        elif n_alto:
            veredicto = (f"La base de medición funciona, pero hay {n_alto} problema(s) "
                         f"grave(s) que están dañando la calidad del dato — el principal: "
                         f"«{peor['title']}». Conviene corregirlos esta semana; cada día "
                         "que pasan activos se pierden conversiones o se ensucian métricas.")
            nivel = "grave"
        else:
            veredicto = (f"La medición funciona correctamente en lo esencial. Hay "
                         f"{len(reales)} ajuste(s) de calidad recomendados que "
                         "mejorarán la fiabilidad y la atribución, sin urgencia crítica.")
            nivel = "mejorable"

    # Plan priorizado
    bloques = []
    grupos = [("🔥 Urgente — corta pérdida de datos", ("critico", "alto")),
              ("⚠️ Importante — mejora la calidad del dato", ("medio",)),
              ("✨ Recomendado — buenas prácticas", ("bajo",))]
    for titulo, sevs in grupos:
        items = [i for i in issues if i["severity"] in sevs]
        if items:
            bloques.append({"titulo": titulo, "items": [{
                "titulo": i["title"],
                "owner": _issue_owner(i),
                "pasos": i["fix_steps"],
            } for i in items]})

    cierre = ("Tras aplicar cada bloque, vuelve a escanear la página para confirmar "
              "que el problema desaparece y que no se ha roto nada más.")
    return {"veredicto": veredicto, "nivel": nivel, "bloques": bloques,
            "cierre": cierre}


def health_score(issues):
    score = 100
    for i in issues:
        score -= SEVERITY_WEIGHT.get(i["severity"], 0)
    return max(0, score)


def score_label(score):
    if score >= 90:
        return "🟢 Excelente"
    if score >= 70:
        return "🟡 Mejorable"
    if score >= 40:
        return "🟠 Con problemas serios"
    return "🔴 Crítico"
