"""Genera el informe de auditoría en HTML con estilo imprimible (para PDF)."""

import html as _html

from .platforms import all_events
from .rules import SEVERITY_LABEL, score_label, attribution_audit, build_action_plan
from .quality import build_inventory, essential_coverage

SEV_COLOR = {"critico": "#c62828", "alto": "#e65100", "medio": "#b8860b",
             "bajo": "#1565c0", "info": "#616161"}

CSS = """
body { font-family: -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
       color: #212121; margin: 0; padding: 32px 40px; font-size: 13px; line-height: 1.55; }
h1 { font-size: 24px; margin: 0 0 2px; }
h2 { font-size: 17px; margin: 28px 0 10px; border-bottom: 2px solid #eee; padding-bottom: 4px; }
h3 { font-size: 14px; margin: 18px 0 6px; }
.sub { color: #757575; margin-bottom: 18px; }
.score { display: inline-block; padding: 8px 18px; border-radius: 10px; background: #f5f5f5;
         font-size: 20px; font-weight: 700; margin: 8px 0 4px; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 14px; }
th, td { border: 1px solid #e0e0e0; padding: 5px 8px; text-align: left; font-size: 12px;
         word-break: break-word; }
th { background: #fafafa; }
.sev { display: inline-block; padding: 1px 10px; border-radius: 10px; color: #fff;
       font-size: 11px; font-weight: 600; margin-right: 6px; }
.issue { border: 1px solid #e6e6e6; border-left-width: 4px; border-radius: 6px;
         padding: 10px 14px; margin: 10px 0; page-break-inside: avoid; }
.issue p { margin: 6px 0; }
.evidence { background: #f7f7f7; border-radius: 4px; padding: 6px 8px;
            font-family: monospace; font-size: 11px; color: #555; }
ol { margin: 6px 0 2px 18px; padding: 0; }
ol li { margin: 3px 0; }
.footer { margin-top: 30px; color: #9e9e9e; font-size: 11px; border-top: 1px solid #eee;
          padding-top: 8px; }
.ok { color: #2e7d32; }
"""


def _e(s):
    return _html.escape(str(s if s is not None else ""))


def build_html_report(url, res):
    scan, det, issues = res["scan"], res["det"], res["issues"]
    cmp_info = res.get("cmp", {}) or {}
    score = res["score"]

    parts = [f"<style>{CSS}</style>"]
    parts.append("<h1>🩺 Pixel Doctor — Informe de medición</h1>")
    parts.append(f"<div class='sub'>{_e(url)} · escaneado el {_e(scan.get('scanned_at'))}</div>")
    parts.append(f"<div class='score'>Salud de medición: {score}/100 — "
                 f"{_e(score_label(score))}</div>")
    if cmp_info.get("cmps"):
        parts.append(f"<div class='sub'>CMP detectado: {_e(', '.join(cmp_info['cmps']))}"
                     + (" · banner aceptado durante el escaneo" if scan.get("consent_click") else "")
                     + "</div>")

    # Conclusión y plan de acción
    plan = build_action_plan(issues, score)
    nivel_color = {"ok": "#2e7d32", "mejorable": "#1565c0",
                   "grave": "#e65100", "critico": "#c62828"}[plan["nivel"]]
    parts.append("<h2>🩺 Conclusión</h2>")
    parts.append(f"<div class='issue' style='border-left-color:{nivel_color}'>"
                 f"<p>{_e(plan['veredicto'])}</p></div>")
    if plan["bloques"]:
        parts.append("<h2>🗺️ Plan de acción priorizado</h2>")
        paso = 0
        for bloque in plan["bloques"]:
            parts.append(f"<h3>{_e(bloque['titulo'])}</h3>")
            for item in bloque["items"]:
                paso += 1
                parts.append(f"<p><strong>{paso}. {_e(item['titulo'])}</strong> — "
                             f"<em>responsable: {_e(item['owner'])}</em></p><ol>")
                for s in item["pasos"]:
                    parts.append(f"<li>{_e(s)}</li>")
                parts.append("</ol>")
        parts.append(f"<p><em>{_e(plan['cierre'])}</em></p>")

    # Plataformas
    parts.append("<h2>Plataformas detectadas</h2>")
    parts.append("<table><tr><th>Plataforma</th><th>IDs</th><th>Librería cargada</th>"
                 "<th>Eventos enviados</th></tr>")
    for d in det.values():
        if not d["detected"]:
            continue
        parts.append(f"<tr><td>{_e(d['name'])}</td><td>{_e(', '.join(d['ids']) or '—')}</td>"
                     f"<td>{'✅' if d['library_loaded'] else '❌'}</td>"
                     f"<td>{len(d['events'])}</td></tr>")
    parts.append("</table>")

    # Plan de medición
    inventory = build_inventory(scan, det)
    coverage = essential_coverage(inventory, det)
    parts.append("<h2>📋 Plan de medición</h2>")
    parts.append("<h3>Cobertura de eventos esenciales</h3>")
    parts.append("<table><tr><th>Evento esencial</th><th>Estado</th>"
                 "<th>Cómo implementarlo (si falta)</th></tr>")
    for c in coverage:
        parts.append(f"<tr><td>{_e(c['evento'])}</td>"
                     f"<td>{'✅ presente' if c['presente'] else '❌ falta'}</td>"
                     f"<td>{'' if c['presente'] else _e(c['como'])}</td></tr>")
    parts.append("</table>")
    if inventory:
        parts.append("<h3>Inventario de eventos detectados</h3>")
        parts.append("<table><tr><th>Evento</th><th>Tipo</th><th>Fuentes</th>"
                     "<th>Propiedades</th></tr>")
        for r in inventory:
            parts.append(f"<tr><td>{_e(r['evento'])}</td><td>{_e(r['tipo'])}</td>"
                         f"<td>{_e(', '.join(r['fuentes']))}</td>"
                         f"<td>{_e(', '.join(r['propiedades']) or '—')}</td></tr>")
        parts.append("</table>")
    parts.append("<p style='color:#757575;font-size:11px'>El escaneo solo carga la "
                 "página: los eventos que disparan con interacción pueden existir y "
                 "no aparecer aquí.</p>")

    # Atribución
    attr_rows = attribution_audit(scan, det)
    if attr_rows:
        simbolo = {True: "✅", False: "❌", None: "—"}
        parts.append("<h2>🎯 Auditoría de atribución (UTM y click-IDs)</h2>")
        parts.append("<table><tr><th>Parámetro</th><th>Sobrevive a la redirección</th>"
                     "<th>Guardado en cookie</th><th>Enviado en hits</th></tr>")
        for r in attr_rows:
            parts.append(f"<tr><td>{_e(r['param'])}</td>"
                         f"<td>{simbolo[r['en_url_final']]}</td>"
                         f"<td>{_e(r['cookie'])} {simbolo[r['cookie_ok']]}</td>"
                         f"<td>{simbolo[r['en_hits']]} {_e(r['hits_de'])}</td></tr>")
        parts.append("</table>")

    # Prueba de lead
    lt = scan.get("lead_test")
    if scan.get("options", {}).get("submit_form"):
        parts.append("<h2>🧪 Prueba de lead (envío de formulario)</h2>")
        if not lt or not lt.get("form_found"):
            parts.append("<p>No se encontró un formulario visible en la página.</p>")
        else:
            parts.append(f"<p>Formulario encontrado · {len(lt.get('fields', []))} campos "
                         f"rellenados · enviado: {'✅ sí' if lt.get('submitted') else '❌ no'}"
                         + (f" ({_e(lt.get('submit_via'))})" if lt.get("submit_via") else "")
                         + "</p>")
            if lt.get("url_after"):
                parts.append(f"<p>URL tras el envío: {_e(lt['url_after'])}</p>")
            evs_submit = [e for e in all_events(det) if e.get("phase") == "post_submit"]
            if evs_submit:
                parts.append("<p class='ok'>Eventos disparados tras el envío:</p>")
                parts.append("<table><tr><th>Plataforma</th><th>Evento</th><th>ID</th>"
                             "<th>HTTP</th></tr>")
                for e in evs_submit:
                    parts.append(f"<tr><td>{_e(e['platform'])}</td><td>{_e(e['event'])}</td>"
                                 f"<td>{_e(e['id'])}</td><td>{_e(e['failure'] or e['status'])}</td></tr>")
                parts.append("</table>")

    # Problemas
    parts.append(f"<h2>Problemas detectados y soluciones ({len(issues)})</h2>")
    if not issues:
        parts.append("<p class='ok'>Sin problemas detectados. ✅</p>")
    for n, iss in enumerate(issues, 1):
        color = SEV_COLOR.get(iss["severity"], "#616161")
        parts.append(f"<div class='issue' style='border-left-color:{color}'>")
        parts.append(f"<h3><span class='sev' style='background:{color}'>"
                     f"{_e(SEVERITY_LABEL[iss['severity']].split(' ', 1)[-1])}</span> "
                     f"{n}. {_e(iss['title'])}</h3>")
        parts.append(f"<p>{_e(iss['description'])}</p>")
        if iss.get("evidence"):
            parts.append(f"<div class='evidence'>{_e(iss['evidence'])}</div>")
        parts.append("<p><strong>Solución paso a paso:</strong></p><ol>")
        for step in iss["fix_steps"]:
            parts.append(f"<li>{_e(step)}</li>")
        parts.append("</ol></div>")

    # Eventos
    evs = all_events(det)
    if evs:
        parts.append(f"<h2>Eventos de medición capturados ({len(evs)})</h2>")
        parts.append("<table><tr><th>Momento</th><th>Plataforma</th><th>Evento</th>"
                     "<th>ID</th><th>HTTP</th><th>Fase</th></tr>")
        fase_lbl = {"pre_consent": "antes de aceptar cookies",
                    "post_consent": "tras aceptar cookies",
                    "post_submit": "tras enviar formulario", "load": "carga"}
        for e in evs:
            parts.append(f"<tr><td>{_e(e['ts'])}s</td><td>{_e(e['platform'])}</td>"
                         f"<td>{_e(e['event'])}</td><td>{_e(e['id'])}</td>"
                         f"<td>{_e(e['failure'] or e['status'] or '')}</td>"
                         f"<td>{_e(fase_lbl.get(e['phase'], e['phase']))}</td></tr>")
        parts.append("</table>")

    parts.append("<div class='footer'>Informe generado automáticamente por Pixel Doctor. "
                 "Las soluciones indican el procedimiento estándar; verifica los cambios "
                 "con la Vista previa de GTM antes de publicar.</div>")
    return "\n".join(parts)
