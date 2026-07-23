# 🩺 Pixel Doctor — Auditor de medición y píxeles

Aplicación Streamlit que escanea cualquier web o landing con un **navegador
Chromium real** y audita toda la implementación de medición:

- **Plataformas**: Google Tag Manager, GA4, Google Ads, Universal Analytics
  (detección de restos obsoletos), Meta Pixel, LinkedIn Insight, TikTok Pixel,
  Microsoft/Bing UET, X/Twitter, Pinterest, Snap, Hotjar, Clarity, HubSpot.
- **Qué detecta**: píxeles instalados pero mudos, eventos duplicados,
  contenedores GTM dobles, IDs faltantes, hits bloqueados (CSP/adblock/CMP),
  errores HTTP en los hits, dataLayer inexistente o sin eventos de conversión,
  Consent Mode denegando la medición, tags que no reaccionan al aceptar cookies,
  cookies creadas antes del consentimiento (riesgo RGPD), y más.
- **Cada problema** incluye severidad, evidencia y **solución paso a paso**.
- **Calidad del plan de medición** (pestaña 📋): inventario de todos los
  eventos detectados, cobertura de eventos esenciales (page_view, cta_clicked,
  form_submitted, conversión) con cómo implementar los que faltan, linter de
  nombres de eventos (espacios, mayúsculas, acentos, genéricos, convenciones
  mezcladas), propiedades de contexto en eventos de conversión, escaneo de PII
  (emails/teléfonos en claro en cualquier hit) e higiene de UTMs (mayúsculas,
  valores genéricos, source sin medium).

## Instalación

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Uso

```bash
streamlit run app.py
```

1. Pega una o varias URLs (una por línea). Consejo: escanea también la página
   de gracias/conversión.
2. Deja activado **"Aceptar banner de consentimiento"** para comparar los hits
   antes y después de aceptar cookies (clave para diagnosticar Consent Mode).
3. Opcional — **🎯 Simular llegada de campaña**: añade `utm_*` + `gclid` +
   `fbclid` + `msclkid` + `ttclid` de prueba a la URL y audita la atribución:
   ¿sobreviven a las redirecciones? ¿se guardan en sus cookies (`_gcl_aw`,
   `_fbc`, `_fbp`, `_uetmsclkid`…)? ¿se envían en los hits a su plataforma?
   También puedes pegar directamente una URL con tus propios UTM/gclid/fbclid:
   se auditan igual.
4. Opcional — **🧪 Prueba de lead**: activa "Rellenar y ENVIAR el formulario"
   para que el escáner envíe el formulario con datos de prueba y compruebe si
   el lead genera eventos (dataLayer + hits de conversión). ⚠️ Crea un lead
   real de prueba en tu CRM: bórralo después.
5. Pulsa **Escanear** y revisa la pestaña *Problemas y soluciones*.
6. Descarga el **informe en PDF** para enviarlo al cliente/desarrollador
   (también disponible en Markdown y JSON).

### CLI (sin interfaz)

```bash
python -m scanner.browser_scan https://ejemplo.com --consent --json resultado.json
```

## Límites conocidos

- El escaneo es de **carga de página** (+scroll opcional): las conversiones que
  disparan al enviar un formulario o comprar no se ven aquí. Para auditarlas,
  escanea la página de gracias o usa la Vista previa de GTM.
- Páginas tras login/VPN no son accesibles.
- El escáner se ejecuta sin adblock: si un hit falla aquí, el problema es de la
  propia web (CSP, CMP o implementación), no del navegador del visitante.

## Despliegue

| | |
|---|---|
| **Repositorio** | `WeRise-ESP/pixel-doctor` (rama `main`) |
| **Plataforma** | **Railway** (build por `Dockerfile`) |
| **Config** | `railway.json` — builder `DOCKERFILE`, reinicio `ON_FAILURE` (máx. 3) |

**Actualizar = `git push` a `main`.** Railway reconstruye y redespliega solo.
El build tarda: instala Chromium y las dependencias de sistema de Playwright.

Se usa Docker precisamente por eso — Playwright necesita un **navegador Chromium
real** con sus librerías de sistema, algo que un despliegue Python plano no cubre.

### Alternativa: Streamlit Cloud
Es posible pero incómodo: hay que crear un `packages.txt` con las dependencias
del sistema y ejecutar `playwright install chromium` en el arranque (p. ej. con
`st.cache_resource` + `subprocess`). El contenedor Docker de Railway lo resuelve
mejor.
