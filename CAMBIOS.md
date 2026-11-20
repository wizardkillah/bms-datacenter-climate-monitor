# Cambios

## Sin publicar — corrección del cliente BACnet

- `bacnet.py` solo acepta la respuesta a la petición en curso (misma IP e invoke-id) y descarta
  las atrasadas. Antes tomaba el primer datagrama: tras un timeout, la respuesta tardía se
  asignaba a la lectura siguiente y cada punto mostraba el valor del anterior. Es la misma
  corrección que ya tenían combustible y gases (regresión del 03-11-2026).
- Nueva prueba `tests/test_bacnet_desfase.py` (3 casos). Total: 36 pruebas.
- Para llevarla al PC del hospital hay que volver a empaquetar (`herramientas/empaquetar.py`).

## 1.1.1 — 2026-09-29 · paquete para el PC del hospital sin .bat

- el antivirus bloquea los `.bat` en el PC del hospital, así que se eliminaron. El arranque es con
  `INICIAR_CLIMA.py`, `INSTALAR.py` y `DIAGNOSTICO.py`, por doble clic, con consola visible.
- `INSTALAR.py` corre las pruebas y el diagnóstico en el mismo proceso, sin procesos hijos.
- El empaquetador rechaza `.bat`, `.cmd`, `.ps1`, `.vbs`, `.pyw`, `.lnk` y `.exe`, y genera
  `ClimaDatacenter_v<versión>_hospital.zip`.
- Instalación de referencia en `C:\Users\operador\Documents\`.
  Se agrega `LEEME_PRIMERO.txt`.

## 1.1.0 — 2026-09-29 · auditoría y preparación para el PC del hospital

- Estructura de paquete: `clima\` (se ejecuta con `python -m clima`), y `web\css|js|img`,
  `datos\`, `logs\`, `vendor\`, `herramientas\` y `docs\`.
- Seguridad: instancia única real en Windows, protección CSRF y DNS rebinding, cabeceras
  CSP y otras, y clave obligatoria si se publica en la red.
- Privacidad: el dashboard, la API y el Excel ya no muestran IPs, MAC ni errores internos.
- Correcciones: la sonda dañada ya no deja sin leer a la sana; un error del FieldServer ya no
  hace perder el ciclo; el FieldServer se consulta sin proxy; el Excel se limita a 50.000 filas.
- el antivirus: sin PowerShell, `mshta`, descargas ni procesos ocultos; `openpyxl` va incluido;
  se agregan `INSTALAR.bat` (quita *Zone.Identifier*) y `DIAGNOSTICO.bat`.
- Registro rotativo en `logs\clima.log`.
- 33 pruebas offline. Detalle en `docs\AUDITORIA_2026-09-29.md`.

## 1.0 — 2026-09-29

- Dashboard del datacenter y la sala eléctrica: sondas BACnet de TCC-D01 y 3 equipos de
  precisión vía FieldServer.
- Límites por sala, cruce entre sondas y equipos, histórico, gráficos y Excel.
