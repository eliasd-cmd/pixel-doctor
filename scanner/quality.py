"""Calidad del plan de medición.

Basado en las buenas prácticas de tracking (convención objeto_accion,
eventos esenciales de una web de marketing, propiedades con contexto,
nada de PII, UTMs consistentes):

1. Linter de nombres de eventos (dataLayer + GA4).
2. Cobertura del plan de medición: eventos esenciales que faltan.
3. Propiedades de los eventos de conversión (contexto y consistencia).
4. Escaneo de PII (emails/teléfonos en claro) en TODOS los hits.
5. Higiene de UTMs de la URL escaneada.
"""

import re
from urllib.parse import urlparse, parse_qsl, unquote

# Eventos que GA4 mide solo / recomendados estándar (no se lintean)
GA4_AUTO_EVENTS = {
    "page_view", "session_start", "first_visit", "user_engagement", "scroll",
    "click", "file_download", "video_start", "video_progress", "video_complete",
    "form_start", "form_submit", "view_search_results", "page_view (implícito)",
}
META_STANDARD = {
    "PageView", "Lead", "Purchase", "CompleteRegistration", "Contact",
    "SubmitApplication", "Subscribe", "StartTrial", "Schedule", "ViewContent",
    "AddToCart", "InitiateCheckout", "AddPaymentInfo", "Search", "AddToWishlist",
    "CustomizeProduct", "Donate", "FindLocation",
}
DL_IGNORE_KEYS = {"event", "gtm.uniqueEventId", "eventCallback", "eventTimeout",
                  "gtm.start", "gtm.element", "gtm.elementClasses", "gtm.elementId"}
GENERIC_NAMES = {"click", "clic", "evento", "event", "submit", "boton", "button",
                 "test", "prueba", "conversion1", "custom_event", "custom"}

# Valores que añade la propia app al simular campaña (no se lintean)
SIMULATED_VALUES = {"pixel-doctor", "auditoria", "test-medicion",
                    "PXDOCTESTGCLID123", "PXDOCTESTFBCLID123",
                    "pxdoctestmsclkid123", "pxdoctestttclid123"}

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+(?:@|%40)[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
TRACK_HOSTS = ("google-analytics", "googletagmanager", "googleadservices",
               "doubleclick", "facebook.com/tr", "px.ads.linkedin",
               "analytics.tiktok", "bat.bing", "clarity.ms", "hotjar",
               "track.hubspot", "analytics.twitter", "ct.pinterest",
               "tr.snapchat", "ccm/collect", "pagead", "/g/collect")

CONVERSION_NAME_RE = re.compile(
    r"lead|conversi|form(_?submit|_?sent|ulario)|submit_?form|contact|demo|"
    r"registro|sign_?up|solicitud|purchase|compra|suscri|subscribe|cotiza",
    re.I)


def _issue(sev, platform, title, description, fix_steps, evidence=""):
    return {"severity": sev, "platform": platform, "title": title,
            "description": description, "fix_steps": fix_steps,
            "evidence": evidence}


# ----------------------------------------------- inventario de eventos ----

def build_inventory(scan, det):
    """Inventario unificado de eventos: nombre, fuentes, propiedades, tipo."""
    inv = {}

    def add(name, source, props=(), kind="personalizado"):
        row = inv.setdefault(str(name), {"evento": str(name), "fuentes": set(),
                                         "propiedades": set(), "tipo": kind})
        row["fuentes"].add(source)
        row["propiedades"].update(p for p in props if p)
        if kind == "automático":
            row["tipo"] = "automático"

    dl = ((scan.get("js") or {}).get("dataLayer")) or []
    if isinstance(dl, list):
        for item in dl:
            if isinstance(item, dict) and item.get("event"):
                ev = str(item["event"])
                if ev.startswith("gtm."):
                    continue
                props = [k for k in item.keys() if k not in DL_IGNORE_KEYS]
                add(ev, "dataLayer", props)

    for e in det["ga4"]["events"]:
        name = e["event"]
        if name.startswith(("ping", "config")):
            continue
        props = [k for k in e.get("params", {}) if k.startswith(("ep.", "epn.", "up."))]
        kind = "automático" if name in GA4_AUTO_EVENTS else "personalizado"
        add(name, "GA4", props, kind)

    for e in det["meta"]["events"]:
        kind = "estándar Meta" if e["event"] in META_STANDARD else "personalizado"
        add(e["event"], "Meta", e.get("params", {}).keys() - {"eid", "fbc", "fbp"}, kind)

    for key, label in (("gads", "Google Ads"), ("linkedin", "LinkedIn"),
                       ("tiktok", "TikTok"), ("bing", "Bing")):
        for e in det[key]["events"]:
            add(e["event"], label, (), "plataforma")

    rows = list(inv.values())
    for r in rows:
        r["fuentes"] = sorted(r["fuentes"])
        r["propiedades"] = sorted(r["propiedades"])
    return sorted(rows, key=lambda r: r["evento"].lower())


# --------------------------------------------------- 1. linter nombres ----

def naming_findings(inventory):
    """Analiza los nombres de eventos personalizados de dataLayer/GA4."""
    names = [r["evento"] for r in inventory
             if r["tipo"] == "personalizado"
             and any(f in ("dataLayer", "GA4") for f in r["fuentes"])]
    findings = []
    for n in names:
        problems = []
        if len(n) > 40:
            problems.append("supera los 40 caracteres (límite de GA4)")
        if re.search(r"\s", n):
            problems.append("contiene espacios")
        if re.search(r"[áéíóúñüÁÉÍÓÚÑÜ]", n):
            problems.append("contiene acentos/ñ (rompe integraciones)")
        if re.search(r"[^a-zA-Z0-9_\s]", n):
            problems.append("contiene caracteres especiales")
        if re.search(r"[A-Z]", n):
            problems.append("usa mayúsculas (GA4 distingue mayúsculas: "
                            "'Lead' y 'lead' serían eventos distintos)")
        if n.lower() in GENERIC_NAMES:
            problems.append("nombre genérico: no dice qué se midió")
        if n and n[0].isdigit():
            problems.append("empieza por número (inválido en GA4)")
        if problems:
            findings.append({"evento": n, "problemas": problems})

    # Convenciones mezcladas en el conjunto
    snake = [n for n in names if re.fullmatch(r"[a-z0-9_]+", n)]
    camel = [n for n in names if re.search(r"[a-z][A-Z]", n)]
    mixed = bool(snake) and bool(camel)
    return findings, mixed, names


# ------------------------------------------- 2. cobertura del plan --------

ESSENTIALS = [
    {"evento": "page_view", "detecta": r"^page_view",
     "descripcion": "Vista de página (automático en GA4)",
     "como": "GA4 lo envía solo con la etiqueta de configuración. Si falta, "
             "revisa que la etiqueta GA4 dispare en All Pages."},
    {"evento": "cta_clicked", "detecta": r"cta|button_?click|clic_|click_",
     "descripcion": "Clic en el CTA principal (mide el interés antes del formulario)",
     "como": "dataLayer.push({event:'cta_clicked', button_text:'…', location:'hero'}) "
             "o activador de clic de GTM sobre el botón + etiqueta GA4."},
    {"evento": "form_submitted", "detecta": r"form(_?submit|_?sent)|submit_?form|form_start",
     "descripcion": "Envío de formulario",
     "como": "dataLayer.push({event:'form_submitted', form_id:'…'}) en el éxito "
             "del formulario, o activador nativo 'Envío de formulario' de GTM."},
    {"evento": "generate_lead (conversión)", "detecta": CONVERSION_NAME_RE.pattern,
     "descripcion": "La conversión principal (lead/registro/compra) enviada a "
                    "GA4 Y a las plataformas de ads",
     "como": "Evento GA4 'generate_lead' marcado como conversión clave + conversión "
             "de Google Ads + 'Lead' de Meta (y LinkedIn/TikTok/Bing si aplica), "
             "todos con el mismo activador."},
]


def essential_coverage(inventory, det):
    names = [r["evento"].lower() for r in inventory]
    # Las conversiones de plataformas también cuentan
    has_platform_conversion = (
        any(e["event"].startswith("conversión") for e in det["gads"]["events"])
        or any(e["event"] in ("Lead", "Purchase", "CompleteRegistration", "Contact")
               for e in det["meta"]["events"]))
    result = []
    for ess in ESSENTIALS:
        found = any(re.search(ess["detecta"], n, re.I) for n in names)
        if "conversión" in ess["evento"] and has_platform_conversion:
            found = True
        result.append({**ess, "presente": found})
    return result


# --------------------------------------------- 3. propiedades -------------

def property_findings(inventory):
    findings = []
    conv_events = [r for r in inventory
                   if r["tipo"] == "personalizado"
                   and CONVERSION_NAME_RE.search(r["evento"])
                   and any(f in ("dataLayer", "GA4") for f in r["fuentes"])]
    sin_props = [r["evento"] for r in conv_events if not r["propiedades"]]

    # Consistencia de nombres de propiedades (snake vs camel)
    all_props = {p for r in inventory for p in r["propiedades"]}
    p_snake = {p for p in all_props if re.fullmatch(r"[a-z0-9_.]+", p)}
    p_camel = {p for p in all_props if re.search(r"[a-z][A-Z]", p)}
    mixed_props = bool(p_snake) and bool(p_camel)
    return sin_props, mixed_props, sorted(p_camel)


# --------------------------------------------------- 4. escaneo PII -------

def pii_scan(scan):
    """Busca emails/teléfonos en claro en los hits de medición."""
    test_email = (scan.get("options", {}).get("test_email") or "").lower()
    hallazgos = []
    for r in scan.get("requests", []):
        if not any(h in r["url"] for h in TRACK_HOSTS):
            continue
        blob = unquote(r["url"] + " " + (r.get("post_data") or ""))
        host = r["url"].split("/")[2] if "://" in r["url"] else "?"
        for m in EMAIL_RE.finditer(blob):
            email = m.group(0).replace("%40", "@").lower()
            if email == test_email:
                continue  # ya lo cubre la regla de la prueba de lead
            if email.endswith((".js", ".css", ".png", ".svg", ".gif", ".jpg")):
                continue  # falso positivo: nombre de fichero versionado
            masked = email[:3] + "…@" + email.split("@")[-1]
            hallazgos.append({"tipo": "email", "dato": masked, "host": host,
                              "fase": r.get("phase", "")})
        for key in ("tel", "phone", "telefono", "movil", "cd[ph]", "ph"):
            for m in re.finditer(
                    rf"[?&]{re.escape(key)}=(\+?\d[\d .\-]{{7,14}})[&\s]", blob):
                hallazgos.append({"tipo": "teléfono", "dato": m.group(1)[:4] + "…",
                                  "host": host, "fase": r.get("phase", "")})
    # dedupe
    seen, out = set(), []
    for h in hallazgos:
        k = (h["tipo"], h["dato"], h["host"])
        if k not in seen:
            seen.add(k)
            out.append(h)
    return out


# --------------------------------------------------- 5. higiene UTM -------

def utm_lint(scan):
    q = dict(parse_qsl(urlparse(scan.get("url", "")).query, keep_blank_values=True))
    utms = {k: v for k, v in q.items()
            if k.startswith("utm_") and v not in SIMULATED_VALUES}
    findings = []
    for k, v in utms.items():
        problems = []
        if re.search(r"[A-Z]", v):
            problems.append(f"tiene mayúsculas — '{v}' y '{v.lower()}' contarían "
                            "como fuentes distintas en GA4")
        if " " in v or "%20" in v:
            problems.append("contiene espacios (se parte en los informes)")
        if v.lower() in ("test", "prueba", "cta1", "link", "banner1", "boton", "1", "click"):
            problems.append("valor genérico: no permite saber qué era")
        if problems:
            findings.append({"param": k, "valor": v, "problemas": problems})
    if utms:
        if "utm_source" in utms and "utm_medium" not in q:
            findings.append({"param": "utm_medium", "valor": "(ausente)",
                             "problemas": ["hay utm_source sin utm_medium: GA4 "
                                           "clasificará la sesión como 'unassigned'"]})
        if "utm_medium" in utms and "utm_source" not in q:
            findings.append({"param": "utm_source", "valor": "(ausente)",
                             "problemas": ["hay utm_medium sin utm_source"]})
    return findings, utms


# ------------------------------------------------------ reglas → issues ---

def quality_rules(scan, det):
    issues = []
    inventory = build_inventory(scan, det)

    # 1. Nombres
    bad_names, mixed, custom_names = naming_findings(inventory)
    hard = [f for f in bad_names
            if any("espacios" in p or "acentos" in p or "40" in p or "número" in p
                   or "especiales" in p for p in f["problemas"])]
    if bad_names:
        ev = "; ".join(f"{f['evento']} → {f['problemas'][0]}" for f in bad_names[:4])
        issues.append(_issue(
            "medio" if hard else "bajo", "plan",
            f"Nombres de eventos con problemas ({len(bad_names)})",
            "Hay eventos que no siguen la convención recomendada "
            "(objeto_accion, en minúsculas, sin espacios ni acentos). Los nombres "
            "inconsistentes fragmentan los informes de GA4 en silencio.",
            ["Revisa la lista completa en la pestaña '📋 Plan de medición'.",
             "Renombra siguiendo objeto_accion: 'generate_lead', 'cta_clicked', "
             "'form_submitted' (minúsculas + guion bajo).",
             "Cambia el nombre en el dataLayer.push del código Y en el activador "
             "de GTM a la vez, y mantén el antiguo unos días si hay informes que "
             "dependen de él.",
             "Documenta la convención para que todo el equipo la use."], ev))
    if mixed:
        issues.append(_issue(
            "bajo", "plan", "Convenciones de nombres mezcladas (snake_case + camelCase)",
            "Conviven eventos tipo 'form_submit' con eventos tipo 'formSubmit': "
            "señal de implementaciones de distintas épocas/equipos.",
            ["Elige una convención (recomendado: snake_case, la de GA4) y migra "
             "el resto de forma gradual.",
             "Aprovecha para eliminar eventos que ya nadie consulta."]))

    # 2. Cobertura
    coverage = essential_coverage(inventory, det)
    missing = [c for c in coverage if not c["presente"]]
    conv_missing = any("conversión" in c["evento"] for c in missing)
    if missing and any(det[k]["detected"] for k in det):
        issues.append(_issue(
            "medio" if conv_missing else "bajo", "plan",
            f"Plan de medición incompleto: faltan {len(missing)} eventos esenciales",
            "Faltan eventos básicos de una web de marketing: "
            + ", ".join(c["evento"] for c in missing) +
            (". Sin evento de conversión no se puede optimizar ninguna campaña."
             if conv_missing else "."),
            [f"{c['evento']}: {c['como']}" for c in missing] +
            ["Nota: este escaneo solo carga la página. Si estos eventos existen "
             "pero disparan con interacción (clic/envío), verifícalos con la "
             "🧪 Prueba de lead o con GTM Vista previa."]))

    # 3. Propiedades
    sin_props, mixed_props, camel_props = property_findings(inventory)
    if sin_props:
        issues.append(_issue(
            "bajo", "plan",
            f"Eventos de conversión sin propiedades de contexto: {', '.join(sin_props[:4])}",
            "El evento dispara pero no lleva contexto (form_id, location, valor…). "
            "Sin propiedades no podrás segmentar qué formulario o CTA convierte.",
            ["Añade propiedades al push: dataLayer.push({event:'…', form_id:'contacto', "
             "form_location:'footer', value:…}).",
             "En GA4, registra las propiedades como dimensiones personalizadas "
             "(Administración → Definiciones personalizadas) o no aparecerán en informes."]))
    if mixed_props:
        issues.append(_issue(
            "bajo", "plan", "Nombres de propiedades inconsistentes (snake_case + camelCase)",
            f"Ejemplos en camelCase: {', '.join(camel_props[:5])}. GA4 los trata como "
            "dimensiones distintas si algún evento usa la otra variante.",
            ["Unifica a snake_case (form_id, button_text…) en todos los push."]))

    # 4. PII
    pii = pii_scan(scan)
    if pii:
        ejemplos = "; ".join(f"{h['tipo']}: {h['dato']} → {h['host']}" for h in pii[:4])
        issues.append(_issue(
            "alto", "plan", f"PII en claro enviada a plataformas de medición ({len(pii)})",
            "Se detectaron emails/teléfonos sin cifrar en los hits. GA4 lo prohíbe "
            "(puede suspender o borrar la propiedad) y en Meta/otros debe ir "
            "hasheado (SHA-256). También es un problema RGPD.",
            ["Localiza el origen: suele ser el email en la URL (?email=… en la página "
             "de gracias, que GA4 captura en page_location) o una etiqueta que lo "
             "añade como parámetro.",
             "Si está en la URL: elimínalo de la redirección o límpialo antes del "
             "page_view (history.replaceState).",
             "Si lo añade una etiqueta: quita el parámetro o hashéalo (conversiones "
             "mejoradas de Google y Advanced Matching de Meta exigen SHA-256).",
             "Tras corregir, vuelve a escanear para confirmar que desaparece."],
            ejemplos))

    # 5. UTMs
    utm_findings, utms = utm_lint(scan)
    if utm_findings:
        ev = "; ".join(f"{f['param']}={f['valor']}: {f['problemas'][0]}"
                       for f in utm_findings[:3])
        issues.append(_issue(
            "medio" if any("mayúsculas" in p or "ausente" in f["valor"]
                           for f in utm_findings for p in f["problemas"]) else "bajo",
            "atribucion", f"UTMs mal construidas ({len(utm_findings)})",
            "Las UTM de esta URL no siguen las convenciones: minúsculas siempre, "
            "source+medium en pareja, valores descriptivos.",
            ["Convención: todo en minúsculas, guiones bajos, específico pero corto "
             "(utm_content='hero_cta', no 'cta1').",
             "utm_source y utm_medium deben ir siempre juntas (google/cpc, "
             "newsletter/email, linkedin/paid_social…).",
             "Documenta las UTM de cada campaña en una hoja compartida para no "
             "crear variantes ('Google', 'google', 'google.com')."], ev))

    return issues, {"inventory": inventory, "coverage": coverage,
                    "naming": bad_names, "mixed_naming": mixed,
                    "sin_props": sin_props, "pii": pii,
                    "utm_findings": utm_findings, "utms": utms}
