# Auditoría de código — Clima Datacenter v1.0 → v1.1.0 (2026-09-29)

Revisión completa del backend, la web y el empaquetado antes de migrar al PC del hospital.
Cada hallazgo marcado *verificado* se reprodujo antes de corregirlo, y cada corrección tiene
su prueba en `tests/test_seguridad.py`.

## Hallazgos y correcciones

| # | Severidad | Hallazgo | Corrección |
|---|---|---|---|
| 1 | **Alta** | *Verificado.* `HTTPServer` activa `SO_REUSEADDR`, y en Windows eso deja que una **segunda instancia abra el mismo puerto 8090 sin error**. Resultado: dos monitores escribiendo la misma base, como ocurrió en otro dashboard | `Servidor` con `allow_reuse_address=False` y `SO_EXCLUSIVEADDRUSE`. El puerto se toma **antes** de sondear o abrir la base. Si está ocupado, la instancia se retira |
| 2 | **Alta** | *Verificado.* CSRF: un `POST` `text/plain` era aceptado, así que cualquier página abierta en el navegador del PC podía reconocer alarmas o cambiar setpoints | Los POST exigen `Content-Type: application/json`, que obliga a un preflight CORS que no se concede, y un `Origin` igual al `Host` |
| 3 | **Media** | *Verificado.* Se aceptaba cualquier cabecera `Host` (DNS rebinding) | Con escucha local, solo se aceptan `localhost`, `127.0.0.1` y `[::1]` |
| 4 | **Media** | La API y el Excel exponían la **IP y la MAC** de la estación, la IP del FieldServer, errores internos con URL y la IP de quien reconocía o ajustaba | `instantanea(publico=True)` y `eventos(publico=True)` omiten esos campos. El Excel ya no los incluye. El origen queda en la base y en `logs\clima.log`, para auditoría local |
| 5 | **Media** | **Bug:** dos lecturas `None` seguidas de la sonda dañada del datacenter hacían que el ciclo diera la estación por caída y **no leyera la sonda sana de la sala eléctrica** | Tras 2 fallos se consulta el objeto `device`: solo si la estación no responde se dejan de esperar timeouts |
| 6 | **Media** | Una respuesta inesperada del FieldServer (`KeyError`/`ValueError`) se propagaba y **se perdía el ciclo completo**, incluido el BACnet | `leer_equipo` convierte todo a `ErrorFieldServer`, y `_leer_equipos` captura cualquier excepción |
| 7 | **Media** | Las peticiones al FieldServer usaban el **proxy del sistema**. En el PC del hospital podrían salir por el proxy y fallar | `urllib` con `ProxyHandler({})` (sin proxy) |
| 8 | **Media** | La interfaz del FieldServer no tiene autenticación y su JSON-RPC permite reconfigurarlo | Lista blanca en el cliente: solo `read` y `read data array`. Cualquier otro método se rechaza antes de salir a la red |
| 9 | **Media** | Un Excel de un año a 1 minuto (~525 mil filas) tardaría minutos y congelaría el equipo (lección de diálisis). Dos descargas simultáneas se sumaban | Tope de 50.000 filas con ajuste automático de la resolución, informado en el Resumen, y un Excel a la vez (`429` si hay otro en curso) |
| 10 | Baja | Ruta estática comparada con `startswith(WEB)`: aceptaba carpetas hermanas como `web2` | Se compara con `WEB + os.sep` |
| 11 | Baja | Un JSON que no fuera objeto (`[1,2]`) provocaba un `500` | Se valida y responde `400` |
| 12 | Baja | `limite` negativo en `/api/eventos` equivalía a `LIMIT -1` (sin límite) | Se acota entre 1 y 5000 |
| 13 | Baja | La clave de ajustes se comparaba con `!=` | `hmac.compare_digest` |
| 14 | Baja | Sin cabeceras de seguridad, y el servidor anunciaba la versión de Python | CSP, `X-Frame-Options DENY`, `nosniff`, `no-referrer`, y `Server` sin la versión de Python |
| 15 | Baja | `ip_local` fijo en `.245` (el laptop de desarrollo) | Vacío: la interfaz se elige automáticamente en cualquier PC |
| 16 | Baja | Salida solo por `print`, sin registro persistente | `logging` con rotación en `logs\clima.log` y registro de los cambios de comunicación |
| 17 | Baja | Se podía publicar en la red sin clave | Con `--host` distinto de local, el programa **se niega** a arrancar sin `clave_ajustes` |
| 18 | Info | `openpyxl` requería `pip install` en el PC destino | Se incluye en `vendor\` (MIT) |

## Revisado sin cambios

- **SQL:** todas las consultas usan parámetros. Los nombres de columnas y tablas son constantes.
- **XSS:** todo texto de configuración, base o equipos pasa por `esc()` antes de ir a `innerHTML`.
  Los valores sin escapar son números formateados o constantes del servidor.
- **Concurrencia:** una sola conexión SQLite (WAL) protegida por `RLock`. La lectura de red del
  FieldServer ocurre fuera del lock.
- **Solo lectura:** BACnet solo usa `ReadProperty`. En el FieldServer, solo métodos de lectura.

## Pruebas

`python -m unittest discover tests` → **33 pruebas**:
- 17 funcionales;
- 16 de auditoría: instancia única, Host, CSRF, `text/plain`, JSON no-objeto, rutas, cabeceras,
  clave, datos sensibles en API/eventos/Excel, sonda muda y estación caída, tope del Excel,
  y método de escritura rechazado.
