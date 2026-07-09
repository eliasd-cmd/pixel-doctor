"""🩺 Pixel Doctor — Auditor de medición y píxeles publicitarios.

Escanea cualquier URL con un navegador real (Chromium headless), detecta
todos los sistemas de medición (GTM, GA4, Google Ads, Meta, LinkedIn,
TikTok, Bing, y más), diagnostica errores de implementación y da el paso
a paso para solucionarlos.

Ejecutar:  streamlit run app.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
from scanner.platforms import (detect_all, all_events, tracking_failures,
                               CORE_PLATFORMS, conversion_events)
from scanner.rules import run_rules, health_score, score_label, SEVERITY_LABEL
from scanner.report_html import build_html_report
from scanner.rules import attribution_audit, build_action_plan
from scanner.quality import build_inventory, essential_coverage, naming_findings, pii_scan

APP_DIR = Path(__file__).parent

st.set_page_config(page_title="Pixel Doctor — Auditor de Medición",
                   page_icon="🩺", layout="wide")

# ---- Protección por contraseña (solo si APP_PASSWORD está definida) -------
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
if APP_PASSWORD and not st.session_state.get("auth_ok"):
    st.title("🩺 Pixel Doctor")
    st.caption("Auditor de medición y píxeles publicitarios")
    with st.form("login"):
        pwd = st.text_input("🔒 Contraseña de acceso", type="password")
        if st.form_submit_button("Entrar", type="primary"):
            if pwd == APP_PASSWORD:
                st.session_state.auth_ok = True
                st.rerun()
            else:
                st.error("Contraseña incorrecta.")
    st.stop()

PLATFORM_ICONS = {
    "gtm": "🏷️", "ga4": "📊", "gads": "🟦", "ua": "⚰️", "meta": "🔵",
    "linkedin": "💼", "tiktok": "🎵", "bing": "🟩", "twitter": "✖️",
    "pinterest": "📌", "snapchat": "👻", "hotjar": "🔥", "clarity": "🔍",
    "hubspot": "🧡", "general": "⚙️", "consentimiento": "🍪", "lead": "🧪",
    "atribucion": "🎯", "plan": "📋",
}


# ------------------------------------------------------------- escaneo ----

def run_scan(url, consent, interact, mobile, wait_s, lead_opts=None, attribution=None):
    cmd = [sys.executable, "-m", "scanner.browser_scan", url,
           "--wait", str(int(wait_s * 1000)), "--json", "-"]
    if consent:
        cmd.append("--consent")
    if interact:
        cmd.append("--interact")
    if mobile:
        cmd.append("--mobile")
    if attribution:
        cmd += ["--attribution", ",".join(attribution)]
    if lead_opts:
        cmd += ["--submit-form",
                "--test-email", lead_opts["email"],
                "--test-name", lead_opts["name"],
                "--test-phone", lead_opts["phone"]]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          cwd=str(APP_DIR), timeout=240)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-2000:] or "el escáner terminó con error")
    return json.loads(proc.stdout)


def make_pdf(url, res):
    """Genera el PDF del informe vía Chromium (subproceso). Devuelve bytes."""
    html = build_html_report(url, res)
    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "informe.html"
        pdf_path = Path(tmp) / "informe.pdf"
        html_path.write_text(html, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "scanner.pdf_export", str(html_path), str(pdf_path)],
            capture_output=True, text=True, cwd=str(APP_DIR), timeout=90)
        if proc.returncode != 0 or not pdf_path.exists():
            raise RuntimeError(proc.stderr[-800:])
        return pdf_path.read_bytes()


def analyze(scan):
    det = detect_all(scan)
    issues, cmp_info = run_rules(scan, det)
    return {"scan": scan, "det": det, "issues": issues, "cmp": cmp_info,
            "score": health_score(issues)}


# ------------------------------------------------------------- informe ----

def build_markdown_report(url, res):
    scan, det, issues = res["scan"], res["det"], res["issues"]
    lines = [f"# Informe de medición — {url}",
             f"*Escaneado: {scan.get('scanned_at')} · Salud: {res['score']}/100 "
             f"({score_label(res['score'])})*", ""]
    lines.append("## Plataformas detectadas\n")
    for k, d in det.items():
        if d["detected"]:
            lines.append(f"- **{d['name']}** — IDs: {', '.join(d['ids']) or '—'} · "
                         f"librería: {'✅' if d['library_loaded'] else '❌'} · "
                         f"eventos enviados: {len(d['events'])}")
    plan = build_action_plan(issues, res["score"], det, scan)
    lines.append("## Diagnóstico\n")
    lines.append(plan["veredicto"] + "\n")
    for p in plan["diagnostico"]:
        lines.append(p + "\n")
    if plan["bloques"]:
        lines.append("## Plan de acción priorizado\n")
        paso = 0
        for bloque in plan["bloques"]:
            lines.append(f"**{bloque['titulo']}**\n")
            for item in bloque["items"]:
                paso += 1
                lines.append(f"{paso}. **{item['titulo']}** — responsable: {item['owner']}")
        lines.append("\n" + plan["cierre"] + "\n")
    lines.append("\n## Problemas y soluciones\n")
    if not issues:
        lines.append("Sin problemas detectados. ✅")
    for i, iss in enumerate(issues, 1):
        lines.append(f"### {i}. [{SEVERITY_LABEL[iss['severity']]}] {iss['title']}")
        lines.append(iss["description"])
        if iss["evidence"]:
            lines.append(f"\n*Evidencia:* `{iss['evidence']}`")
        lines.append("\n**Solución paso a paso:**")
        for n, step in enumerate(iss["fix_steps"], 1):
            lines.append(f"{n}. {step}")
        lines.append("")
    evs = all_events(det)
    if evs:
        lines.append("## Eventos capturados\n")
        lines.append("| Momento | Plataforma | Evento | ID | Estado | Fase |")
        lines.append("|---|---|---|---|---|---|")
        for e in evs:
            lines.append(f"| {e['ts']}s | {e['platform']} | {e['event']} | {e['id']} "
                         f"| {e['failure'] or e['status'] or ''} | {e['phase']} |")
    return "\n".join(lines)


# ------------------------------------------------------------------ UI ----

st.title("🩺 Pixel Doctor")
st.caption("Auditor de medición: detecta errores en píxeles, etiquetas, dataLayer y "
           "eventos de **Google Ads · GA4 · GTM · Meta · LinkedIn · TikTok · Bing** "
           "y más — con el paso a paso para solucionarlos.")

# Permite lanzar un escaneo desde la URL: ?url=https://ejemplo.com&auto=1
qp_url = st.query_params.get("url", "")
qp_auto = st.query_params.get("auto", "") == "1"

with st.sidebar:
    st.header("⚙️ Escaneo")
    urls_text = st.text_area(
        "URL(s) a auditar", value=qp_url,
        placeholder="https://tulanding.com\nhttps://tuweb.com/gracias",
        help="Una por línea. Consejo: escanea también la página de gracias/conversión.")
    consent = st.toggle("🍪 Aceptar banner de consentimiento", value=True,
                        help="Acepta el banner de cookies automáticamente y compara los hits "
                             "de antes y después. Imprescindible para diagnosticar Consent Mode.")
    interact = st.toggle("🖱️ Hacer scroll (tags de scroll)", value=True)
    mobile = st.toggle("📱 Emular móvil", value=False)
    wait_s = st.slider("Espera tras cargar (segundos)", 3, 15, 6,
                       help="Tiempo para que disparen los tags lentos.")
    attribution = st.toggle(
        "🎯 Simular llegada de campaña", value=False,
        help="Añade a la URL los parámetros que pondría la plataforma elegida "
             "(UTMs + click-ID) y comprueba que sobreviven a redirecciones, generan "
             "su cookie (_gcl_aw, _fbc, li_fat_id…) y se envían en los hits.")
    attr_platforms = []
    if attribution:
        SIM_OPTIONS = {"Google Ads (gclid)": "google", "Meta (fbclid)": "meta",
                       "LinkedIn (li_fat_id)": "linkedin", "TikTok (ttclid)": "tiktok",
                       "Microsoft/Bing (msclkid)": "bing"}
        sel = st.multiselect("Plataformas a simular", list(SIM_OPTIONS),
                             default=list(SIM_OPTIONS),
                             help="Elige una para simular esa campaña con sus UTMs "
                                  "reales (p. ej. google/cpc), o varias a la vez.")
        attr_platforms = [SIM_OPTIONS[s] for s in sel]
    st.divider()
    st.subheader("🧪 Prueba de lead")
    do_lead = st.toggle("Rellenar y ENVIAR el formulario", value=False,
                        help="Rellena el formulario de la página con datos de prueba y lo envía "
                             "de verdad, para comprobar si el lead genera eventos de medición "
                             "(dataLayer + conversiones).")
    if do_lead:
        st.warning("⚠️ Crea un envío REAL: llegará un lead de prueba a tu CRM/email. "
                   "Bórralo después.", icon="⚠️")
        lead_email = st.text_input("Email de prueba", "test+medicion@ejemplo.com")
        lead_name = st.text_input("Nombre de prueba", "Prueba Medicion")
        lead_phone = st.text_input("Teléfono de prueba", "+34600000000")
    go = st.button("🔍 Escanear", type="primary", use_container_width=True)
    st.divider()
    st.caption("El escáner usa un navegador Chromium real: captura las peticiones de red "
               "exactamente como las vería la plataforma publicitaria.")

if "results" not in st.session_state:
    st.session_state.results = {}

if qp_auto and qp_url and not st.session_state.results:
    go = True

if go:
    urls = [u.strip() for u in (urls_text or "").splitlines() if u.strip()]
    if not urls:
        st.error("Introduce al menos una URL.")
    else:
        st.session_state.results = {}
        lead_opts = ({"email": lead_email, "name": lead_name, "phone": lead_phone}
                     if do_lead else None)
        for url in urls:
            u = url if url.startswith("http") else "https://" + url
            with st.spinner(f"Escaneando {u} … (30-90 s)"):
                try:
                    scan = run_scan(u, consent, interact, mobile, wait_s, lead_opts,
                                    attr_platforms if attribution else None)
                    st.session_state.results[u] = analyze(scan)
                except Exception as e:
                    st.session_state.results[u] = {"error": str(e)}

results = st.session_state.results
if not results:
    st.info("👈 Introduce la URL de tu web o landing y pulsa **Escanear**.")
    with st.expander("¿Qué comprueba esta herramienta?"):
        st.markdown("""
- **Presencia y carga real** de GTM, GA4, Google Ads, Meta Pixel, LinkedIn Insight,
  TikTok Pixel, Microsoft/Bing UET, X/Twitter, Pinterest, Snap, Hotjar, Clarity y HubSpot.
- **Eventos realmente enviados** a cada plataforma (hits de red capturados con un navegador real).
- **Errores típicos**: píxel instalado pero mudo, page_view/PageView duplicados, doble contenedor
  GTM, Universal Analytics obsoleto, IDs faltantes, hits bloqueados (CSP/adblock), errores HTTP.
- **Consentimiento**: detecta tu CMP, acepta el banner, compara hits antes/después y diagnostica
  integraciones Consent Mode rotas (hits con `gcs` denegado, tags que no reaccionan al aceptar).
- **dataLayer**: existencia, contenido y ausencia de eventos personalizados de conversión.
- Cada problema incluye **severidad, evidencia y solución paso a paso**.
        """)
    st.stop()

tabs_urls = st.tabs([u.replace("https://", "").replace("http://", "")[:40] for u in results])

for tab, (url, res) in zip(tabs_urls, results.items()):
    with tab:
        if "error" in res:
            st.error(f"No se pudo escanear **{url}**:\n\n```{res['error']}```")
            continue

        scan, det, issues, cmp_info = res["scan"], res["det"], res["issues"], res["cmp"]
        score = res["score"]

        # ------- cabecera
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("🩺 Salud de medición", f"{score}/100", score_label(score))
        n_detected = sum(1 for d in det.values() if d["detected"])
        c2.metric("Plataformas detectadas", n_detected)
        evs = all_events(det)
        c3.metric("Eventos capturados", len(evs))
        crit = sum(1 for i in issues if i["severity"] in ("critico", "alto"))
        c4.metric("Problemas graves", crit)
        c5.metric("CMP", ", ".join(cmp_info["cmps"]) or "No detectado")
        if scan.get("consent_click"):
            st.caption(f"🍪 Banner aceptado automáticamente ({scan['consent_click']}) — "
                       "los hits están separados en fase pre/post consentimiento.")

        # ------- resumen de plataformas core
        st.subheader("Estado por plataforma")
        cols = st.columns(len(CORE_PLATFORMS))
        for col, key in zip(cols, CORE_PLATFORMS):
            d = det[key]
            n_ev = len(d["events"])
            n_conv = len(conversion_events(det, key))
            if not d["detected"]:
                estado, delta = "—", "no instalado"
            elif key == "gtm":
                # GTM es un contenedor: no envía eventos propios
                estado = "✅ OK" if d["library_loaded"] else "🚨 No carga"
                delta = ", ".join(d["ids"][:2]) or "—"
            elif n_ev > 0 and any(e["failure"] or (e["status"] or 0) >= 400 for e in d["events"]):
                estado, delta = "⚠️ Con errores", f"{n_ev} eventos"
            elif n_ev > 0 and n_conv > 0:
                estado, delta = "✅ OK", f"{n_ev} eventos · lead ✓"
            elif n_ev > 0:
                # Envía tráfico pero ningún evento de lead/conversión
                estado, delta = "🟡 Solo PageView", "sin evento de lead"
            else:
                estado, delta = "🚨 Mudo", "0 eventos"
            col.metric(f"{PLATFORM_ICONS[key]} {d['name'].split(' (')[0]}", estado, delta,
                       delta_color="off")

        base_tabs = ["🚨 Problemas y soluciones", "🧩 Plataformas", "⚡ Eventos",
                     "📚 dataLayer", "🍪 Cookies y consentimiento", "🌐 Red (hits)"]
        lead_ran = scan.get("options", {}).get("submit_form")
        attr_rows = attribution_audit(scan, det)
        extra = ["📋 Plan de medición"]
        if attr_rows:
            extra.append("🎯 Atribución")
        if lead_ran:
            extra.append("🧪 Prueba de lead")
        tabs = st.tabs([base_tabs[0]] + extra + base_tabs[1:])
        t1 = tabs[0]
        t_plan = tabs[1]
        idx = 2
        t_attr = t_lead = None
        if attr_rows:
            t_attr = tabs[idx]; idx += 1
        if lead_ran:
            t_lead = tabs[idx]; idx += 1
        t2, t3, t4, t5, t6 = tabs[idx:idx + 5]

        # ------- problemas
        with t1:
            plan = build_action_plan(issues, score, det, scan)
            st.subheader("🩺 Diagnóstico")
            {"ok": st.success, "mejorable": st.info,
             "grave": st.warning, "critico": st.error}[plan["nivel"]](plan["veredicto"])
            for p in plan["diagnostico"]:
                st.markdown(p)
            if plan["bloques"]:
                st.subheader("🗺️ Plan de acción priorizado")
                paso = 0
                for bloque in plan["bloques"]:
                    st.markdown(f"**{bloque['titulo']}**")
                    for item in bloque["items"]:
                        paso += 1
                        st.markdown(f"{paso}. **{item['titulo']}** · _responsable: "
                                    f"{item['owner']}_")
                st.caption(plan["cierre"] + " El detalle de cada punto, con su paso "
                           "a paso completo, está en las fichas de abajo. ⬇️")
                st.divider()
            if not issues:
                st.success("No se detectó ningún problema. La medición se ve sana. 🎉")
            for iss in issues:
                icon = PLATFORM_ICONS.get(iss["platform"], "⚙️")
                with st.expander(f"{SEVERITY_LABEL[iss['severity']]} · {icon} "
                                 f"{iss['title']}",
                                 expanded=iss["severity"] in ("critico", "alto")):
                    st.markdown(iss["description"])
                    if iss["evidence"]:
                        st.code(iss["evidence"], language=None)
                    st.markdown("**🛠️ Solución paso a paso:**")
                    for n, step in enumerate(iss["fix_steps"], 1):
                        st.markdown(f"{n}. {step}")
            try:
                if "pdf" not in res:
                    with st.spinner("Generando PDF…"):
                        res["pdf"] = make_pdf(url, res)
                st.download_button(
                    "📄 Descargar informe en PDF (para enviar)",
                    res["pdf"], file_name="informe_medicion.pdf",
                    mime="application/pdf", type="primary", key=f"pdf_{url}")
            except Exception as e:
                st.caption(f"No se pudo generar el PDF: {e}")
            st.download_button(
                "⬇️ Descargar informe (Markdown)",
                build_markdown_report(url, res),
                file_name="informe_medicion.md", key=f"md_{url}")
            st.download_button(
                "⬇️ Descargar datos del escaneo (JSON)",
                json.dumps({"det": {k: {**d, "ids": list(d["ids"])} if isinstance(d.get("ids"), set) else d
                                    for k, d in det.items()},
                            "issues": issues}, ensure_ascii=False, default=str),
                file_name="escaneo_medicion.json", key=f"js_{url}")

        # ------- plan de medición
        with t_plan:
            inventory = build_inventory(scan, det)
            coverage = essential_coverage(inventory, det)
            st.markdown("**✅ Cobertura de eventos esenciales** (web de marketing):")
            st.dataframe(pd.DataFrame([{
                "Evento esencial": c["evento"],
                "Estado": "✅ presente" if c["presente"] else "❌ falta",
                "Qué mide": c["descripcion"],
                "Cómo implementarlo": "" if c["presente"] else c["como"],
            } for c in coverage]), use_container_width=True, hide_index=True)
            st.caption("Este escaneo solo carga la página: los eventos de interacción "
                       "(clic/envío) pueden existir y no verse aquí — verifícalos con la "
                       "🧪 Prueba de lead o GTM Vista previa.")

            st.markdown("**📋 Inventario de eventos detectados** (plan de medición actual):")
            if inventory:
                st.dataframe(pd.DataFrame([{
                    "Evento": r["evento"],
                    "Tipo": r["tipo"],
                    "Fuentes": ", ".join(r["fuentes"]),
                    "Propiedades": ", ".join(r["propiedades"]) or "—",
                } for r in inventory]), use_container_width=True, hide_index=True)
            else:
                st.warning("No se detectó ningún evento.")

            bad_names, mixed_conv, _ = naming_findings(inventory)
            if bad_names:
                st.markdown("**✏️ Nombres de eventos con problemas:**")
                st.dataframe(pd.DataFrame([{
                    "Evento": f["evento"],
                    "Problemas": "; ".join(f["problemas"]),
                } for f in bad_names]), use_container_width=True, hide_index=True)
            elif inventory:
                st.success("Los nombres de eventos siguen la convención. ✅")

            pii = pii_scan(scan)
            if pii:
                st.markdown("**🔐 PII detectada en hits de medición:**")
                st.dataframe(pd.DataFrame(pii), use_container_width=True,
                             hide_index=True)

        # ------- atribución
        if attr_rows:
            with t_attr:
                st.markdown("**Recorrido de cada parámetro de campaña** — desde la URL "
                            "de entrada hasta la plataforma:")
                simbolo = {True: "✅", False: "❌", None: "—"}
                st.dataframe(pd.DataFrame([{
                    "Parámetro": r["param"], "Valor": r["valor"][:28],
                    "Sobrevive a la redirección": simbolo[r["en_url_final"]],
                    "Guardado en cookie": f"{r['cookie']} {simbolo[r['cookie_ok']]}",
                    "Enviado en hits": f"{simbolo[r['en_hits']]} {r['hits_de']}",
                } for r in attr_rows]), use_container_width=True, hide_index=True)
                st.markdown("**🍪 Cookies de atribución presentes:**")
                attr_cookie_names = ("_gcl_au", "_gcl_aw", "_gcl_gb", "_fbc", "_fbp",
                                     "_uetmsclkid", "_uetsid", "_uetvid", "ttclid",
                                     "_ttp", "li_fat_id", "_ga")
                acs = [c for c in scan.get("cookies", [])
                       if any(c["name"].startswith(n) for n in attr_cookie_names)]
                if acs:
                    st.dataframe(pd.DataFrame([{
                        "Cookie": c["name"], "Dominio": c["domain"],
                        "Valor": (c.get("value") or "")[:70],
                    } for c in acs]), use_container_width=True, hide_index=True)
                else:
                    st.warning("No se creó ninguna cookie de atribución "
                               "(_gcl_*, _fbc, _fbp, _uet*, _ttp…).")
                st.caption("Si escaneaste sin aceptar el consentimiento, es normal que "
                           "falten cookies: repite con 🍪 activado para el diagnóstico real. "
                           "Los problemas detectados aparecen en 'Problemas y soluciones'.")

        # ------- prueba de lead
        if lead_ran:
            with t_lead:
                lt = scan.get("lead_test") or {}
                if not lt.get("form_found"):
                    st.warning("No se encontró un formulario visible en la página. "
                               "Si abre en popup o es un iframe de terceros "
                               "(HubSpot/Typeform), pruébalo manualmente.")
                else:
                    lc1, lc2, lc3 = st.columns(3)
                    lc1.metric("Formulario", "✅ Encontrado")
                    lc2.metric("Campos rellenados", len(lt.get("fields", [])))
                    lc3.metric("Enviado", "✅ Sí" if lt.get("submitted") else "❌ No",
                               lt.get("submit_via") or "", delta_color="off")
                    if lt.get("error"):
                        st.error(lt["error"])
                    if lt.get("fields"):
                        st.dataframe(pd.DataFrame(lt["fields"]),
                                     use_container_width=True, hide_index=True)
                    if lt.get("url_after"):
                        cambio = lt["url_after"].rstrip("/") != scan["url"].rstrip("/")
                        st.markdown(f"**URL tras el envío:** `{lt['url_after']}` "
                                    + ("(→ página de gracias/redirección)" if cambio
                                       else "(sin redirección)"))
                    # dataLayer nuevo tras el envío
                    dl_pre = ((scan.get("js_pre_submit") or {}).get("dataLayer_length")) or 0
                    dl_post = (scan.get("js") or {}).get("dataLayer") or []
                    dl_new = dl_post[dl_pre:] if isinstance(dl_post, list) else []
                    st.markdown("**📚 Entradas nuevas en el dataLayer tras el envío:**")
                    if dl_new:
                        st.json(dl_new, expanded=True)
                    else:
                        st.warning("El dataLayer no recibió ninguna entrada nueva tras "
                                   "enviar el formulario.")
                    # hits tras el envío
                    evs_submit = [e for e in evs if e["phase"] == "post_submit"]
                    st.markdown("**⚡ Hits enviados a plataformas tras el envío:**")
                    if evs_submit:
                        st.dataframe(pd.DataFrame([{
                            "Plataforma": e["platform"], "Evento": e["event"],
                            "ID": e["id"], "HTTP": e["failure"] or e["status"],
                        } for e in evs_submit]), use_container_width=True, hide_index=True)
                    else:
                        st.error("Ninguna plataforma recibió eventos tras el envío — "
                                 "revisa el diagnóstico en 'Problemas y soluciones'.")

        # ------- plataformas
        with t2:
            rows = []
            for d in det.values():
                rows.append({
                    "Plataforma": f"{PLATFORM_ICONS.get(d['key'],'')} {d['name']}",
                    "Detectada": "✅" if d["detected"] else "—",
                    "IDs": ", ".join(d["ids"]) or "—",
                    "Librería cargada": "✅" if d["library_loaded"] else ("❌" if d["detected"] else "—"),
                    "En HTML": "✅" if d["in_html"] else "—",
                    "Eventos enviados": len(d["events"]),
                    "Evento de lead": ("✅" if conversion_events(det, d["key"])
                                       else ("❌" if d["events"] else "—")),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            gkeys = ((scan.get("js") or {}).get("globals") or {}).get("google_tag_manager")
            if gkeys:
                st.caption(f"Tags registrados en `google_tag_manager`: {', '.join(gkeys)}")

        # ------- eventos
        with t3:
            if not evs:
                st.warning("No se capturó ningún evento de medición.")
            else:
                df = pd.DataFrame([{
                    "⏱️ s": e["ts"], "Plataforma": e["platform"], "Evento": e["event"],
                    "ID": e["id"], "HTTP": e["failure"] or e["status"],
                    "Fase": {"pre_consent": "🔒 pre-consent", "post_consent": "🍪 post-consent",
                             "post_submit": "🧪 post-envío",
                             "load": "carga"}.get(e["phase"], e["phase"]),
                    "Parámetros": ", ".join(f"{k}={v}" for k, v in list(e["params"].items())[:6]),
                } for e in evs])
                st.dataframe(df, use_container_width=True, hide_index=True)
                if scan.get("options", {}).get("consent") and scan.get("consent_click"):
                    pre = sum(1 for e in evs if e["phase"] == "pre_consent")
                    post = sum(1 for e in evs if e["phase"] == "post_consent")
                    st.caption(f"🔒 Antes de aceptar: **{pre}** hits · 🍪 después de aceptar: **{post}** hits")

        # ------- dataLayer
        with t4:
            dl = (scan.get("js") or {}).get("dataLayer")
            if dl is None:
                st.error("`window.dataLayer` no está definido en esta página.")
            elif isinstance(dl, str):
                st.warning(dl)
            else:
                dl_events = [e.get("event") for e in dl if isinstance(e, dict) and e.get("event")]
                st.markdown(f"**{len(dl)} entradas** · eventos: "
                            f"`{'`, `'.join(map(str, dl_events)) or '—'}`")
                st.json(dl, expanded=False)

        # ------- cookies / consent
        with t5:
            cc1, cc2 = st.columns(2)
            with cc1:
                st.markdown("**🔒 Cookies antes del consentimiento**")
                pre = scan.get("cookies_pre_consent", [])
                st.dataframe(pd.DataFrame(pre) if pre else pd.DataFrame({"—": ["ninguna"]}),
                             use_container_width=True, hide_index=True)
            with cc2:
                st.markdown("**🍪 Cookies al final del escaneo**")
                post = scan.get("cookies", [])
                st.dataframe(pd.DataFrame(post) if post else pd.DataFrame({"—": ["ninguna"]}),
                             use_container_width=True, hide_index=True)
            st.markdown(f"**CMP detectado:** {', '.join(cmp_info['cmps']) or 'ninguno'} · "
                        f"**API TCF:** {'✅' if cmp_info['tcf_api'] else '—'} · "
                        f"**Banner aceptado por el escáner:** {scan.get('consent_click') or 'no'}")

        # ------- red
        with t6:
            fails = tracking_failures(scan)
            if fails:
                st.error(f"**{len(fails)} peticiones de tracking con problemas:**")
                st.dataframe(pd.DataFrame([{
                    "URL": f["url"][:140], "Problema": f["problema"], "Fase": f["phase"],
                } for f in fails]), use_container_width=True, hide_index=True)
            with st.expander(f"Todas las peticiones de red ({len(scan['requests'])})"):
                st.dataframe(pd.DataFrame([{
                    "⏱️ s": r["ts"], "Método": r["method"], "HTTP": r["failure"] or r["status"],
                    "Tipo": r["resource_type"], "URL": r["url"][:160],
                } for r in scan["requests"]]), use_container_width=True, hide_index=True)
            console = scan.get("console", [])
            errors_console = [c for c in console if c["type"] in ("error", "warning")]
            if errors_console:
                with st.expander(f"Consola: errores y avisos ({len(errors_console)})"):
                    for c in errors_console[:80]:
                        st.code(f"[{c['type']}] {c['text']}", language=None)
