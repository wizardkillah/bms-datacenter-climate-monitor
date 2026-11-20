"""Servidor HTTP del dashboard (biblioteca estándar).

    python -m clima [--puerto 8090] [--host 127.0.0.1] [--sin-navegador]

Por defecto escucha solo en 127.0.0.1. Publicarlo en la red (--host 0.0.0.0)
exige definir "clave_ajustes" en config/datacenter.json.

API (todas las respuestas omiten IP, MAC y errores internos):
    GET  /api/estado                     estado vivo, alarmas, equipos, cruce y ajustes
    GET  /api/historial?horas=24         series agregadas (o desde=&hasta=)
    GET  /api/eventos?limite=200
    GET  /api/excel?desde=&hasta=&paso=  descarga .xlsx (paso en segundos)
    GET  /api/salud
    POST /api/ajustes                    {"zonas": {...}, ..., "clave": ""}
    POST /api/reconocer                  {"clave": "s1.temperatura"} o {} = todas
"""
import hmac
import json
import logging
import mimetypes
import os
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import DIR_WEB, __version__, monitor

log = logging.getLogger("clima.web")

LOGO_SVG = os.path.join(DIR_WEB, "img", "logo_hospital.svg")
HOSTS_LOCALES = {"localhost", "127.0.0.1", "[::1]", "::1"}
DEMO = False
BANNER_DEMO = ('<div class="demo-banner">MODO DEMOSTRACIÓN · valores de ejemplo, '
               'no son lecturas en vivo</div>')
MON = None                      # MonitorClima, se crea en main()
_EXCEL = threading.Semaphore(1)  # un Excel a la vez: es la operacion mas pesada


def _logo():
    # Se lee en cada carga: el hospital puede reemplazar el SVG sin reiniciar
    try:
        with open(LOGO_SVG, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ('<div class="logo-texto">Hospital Demo'
                '</div>')


def _fecha(txt):
    """Acepta epoch o fechas ISO 'YYYY-MM-DD' / 'YYYY-MM-DDTHH:MM'."""
    txt = (txt or "").strip()
    if not txt:
        return None
    if txt.isdigit():
        return int(txt)
    return int(datetime.fromisoformat(txt).timestamp())


def _rango(q, horas_def=24):
    desde = _fecha(q.get("desde", [""])[0])
    hasta = _fecha(q.get("hasta", [""])[0])
    if desde is None:
        horas = float(q.get("horas", [str(horas_def)])[0])
        hasta = int(time.time())
        desde = hasta - int(horas * 3600)
    elif hasta is None:
        hasta = int(time.time())
    elif len(q.get("hasta", [""])[0].strip()) == 10:
        hasta += 86399                       # 'hasta' dia completo incluido
    if hasta <= desde:
        raise ValueError("el rango de fechas está invertido")
    return desde, hasta


class Servidor(ThreadingHTTPServer):
    # HTTPServer activa SO_REUSEADDR, y en Windows eso deja que una segunda
    # instancia abra el MISMO puerto sin error: dos monitores escribiendo la
    # misma base. Se desactiva y se pide uso exclusivo del puerto.
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Manejador(BaseHTTPRequestHandler):
    server_version = f"ClimaDatacenter/{__version__}"
    sys_version = ""                         # no anunciar la version de Python

    def log_message(self, formato, *args):
        linea = formato % args if args else formato
        if "/api/" in linea and "/api/estado" not in linea and "/api/historial" not in linea:
            log.info("%s %s", self.address_string(), linea)

    # ---------- seguridad ----------

    def _host_valido(self):
        """Evita DNS rebinding: con escucha local, el Host debe ser local."""
        if self.server.server_address[0] not in ("127.0.0.1", "::1"):
            return True
        host = (self.headers.get("Host") or "").strip().lower()
        nombre = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
        return nombre in HOSTS_LOCALES

    def _origen_valido(self):
        """Evita CSRF: un POST debe venir de esta misma pagina."""
        origen = self.headers.get("Origin")
        if not origen:
            return True                        # clientes no-navegador (curl)
        return urllib.parse.urlparse(origen).netloc.lower() == \
            (self.headers.get("Host") or "").lower()

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; "
                         "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        super().end_headers()

    # ---------- respuestas ----------

    def _json(self, datos, codigo=200):
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _bytes(self, cuerpo, tipo, extra=None):
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-cache")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(cuerpo)

    def _archivo(self, ruta):
        if ruta in ("", "/"):
            ruta = "/index.html"
        destino = os.path.normpath(os.path.join(DIR_WEB, ruta.lstrip("/")))
        if not destino.startswith(DIR_WEB + os.sep) or not os.path.isfile(destino):
            return self._json({"error": "no encontrado"}, 404)
        with open(destino, "rb") as f:
            cuerpo = f.read()
        if destino.endswith("index.html"):
            cuerpo = (cuerpo.decode("utf-8")
                      .replace("@@LOGO_SVG@@", _logo())
                      .replace("@@VERSION@@", __version__)
                      .replace("</header>", "</header>" + (BANNER_DEMO if DEMO else ""), 1)).encode("utf-8")
        tipo = mimetypes.guess_type(destino)[0] or "application/octet-stream"
        if tipo.startswith("text/") or tipo.endswith("javascript"):
            tipo += "; charset=utf-8"
        self._bytes(cuerpo, tipo)

    def _cuerpo(self):
        # Solo JSON: un formulario o un fetch "simple" de otra pagina no puede
        # enviar application/json sin preflight CORS, que aqui no se concede.
        tipo = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if tipo != "application/json":
            raise PermissionError("se requiere Content-Type application/json")
        largo = int(self.headers.get("Content-Length") or 0)
        if largo > 64 * 1024:
            raise ValueError("solicitud demasiado grande")
        datos = json.loads(self.rfile.read(largo) or b"{}")
        if not isinstance(datos, dict):
            raise ValueError("se esperaba un objeto JSON")
        return datos

    # ---------- GET ----------

    def do_GET(self):
        if not self._host_valido():
            return self._json({"error": "host no permitido"}, 403)
        partes = urllib.parse.urlparse(self.path)
        ruta, q = partes.path, urllib.parse.parse_qs(partes.query)
        try:
            if ruta == "/api/estado":
                return self._json(MON.instantanea())
            if ruta == "/api/historial":
                desde, hasta = _rango(q)
                return self._json(MON.historial(desde, hasta))
            if ruta == "/api/eventos":
                limite = max(1, min(int(q.get("limite", ["200"])[0]), 5000))
                return self._json({"eventos": MON.eventos(limite)})
            if ruta == "/api/excel":
                return self._excel(q)
            if ruta == "/api/salud":
                inst = MON.instantanea()
                return self._json({
                    "version": __version__, "ultimo_ciclo": inst["ts"],
                    "duracion_ciclo": inst["duracion_ciclo"],
                    "intervalo_seg": MON.intervalo,
                    "guardar_cada_seg": MON.guardar_cada,
                    "retencion_dias": MON.retencion_dias,
                    "comunicacion_sondas": inst["comunicacion"],
                    "comunicacion_equipos": (inst["fieldserver"] or {}).get("comunicacion"),
                    "requiere_clave": bool(MON.cfg.get("clave_ajustes")),
                })
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        except Exception:
            log.exception("error en %s", ruta)
            return self._json({"error": "error interno"}, 500)
        if not ruta.startswith("/api/"):
            return self._archivo(ruta)
        return self._json({"error": "ruta desconocida"}, 404)

    def _excel(self, q):
        from . import excel                   # openpyxl solo si se usa
        desde, hasta = _rango(q, 24 * 7)
        paso = max(60, int(q.get("paso", ["60"])[0]))
        if not _EXCEL.acquire(blocking=False):
            return self._json({"error": "ya se está generando otro Excel; "
                                        "espere unos segundos"}, 429)
        try:
            t0 = time.time()
            datos, nombre = excel.generar(MON, desde, hasta, paso)
            log.info("Excel %s generado en %.1f s (%d kB)", nombre,
                     time.time() - t0, len(datos) // 1024)
        finally:
            _EXCEL.release()
        return self._bytes(datos, "application/vnd.openxmlformats-"
                           "officedocument.spreadsheetml.sheet",
                           {"Content-Disposition": f'attachment; filename="{nombre}"'})

    # ---------- POST ----------

    def do_POST(self):
        if not self._host_valido() or not self._origen_valido():
            return self._json({"error": "origen no permitido"}, 403)
        ruta = urllib.parse.urlparse(self.path).path
        try:
            datos = self._cuerpo()
            origen = self.address_string()
            if ruta == "/api/ajustes":
                clave = MON.cfg.get("clave_ajustes") or ""
                recibida = str(datos.pop("clave", "") or "")
                if clave and not hmac.compare_digest(recibida.encode(), clave.encode()):
                    log.warning("clave de ajustes incorrecta desde %s", origen)
                    return self._json({"error": "clave incorrecta"}, 403)
                aj = MON.cambiar_ajustes(datos, origen)
                return self._json({"ok": True, "ajustes": aj})
            if ruta == "/api/reconocer":
                n = MON.reconocer(datos.get("clave"), origen)
                if n:
                    log.info("%d alarma(s) reconocida(s) desde %s", n, origen)
                return self._json({"ok": True, "reconocidas": n})
        except PermissionError as e:
            return self._json({"error": str(e)}, 415)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        except Exception:
            log.exception("error en %s", ruta)
            return self._json({"error": "error interno"}, 500)
        return self._json({"error": "ruta desconocida"}, 404)


def main(argv=None):
    import argparse
    from . import registro
    ap = argparse.ArgumentParser(prog="clima", description="Dashboard de clima del datacenter")
    ap.add_argument("--puerto", type=int, default=8090)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--sin-navegador", action="store_true",
                    help="no abrir el navegador al arrancar")
    ap.add_argument("--demo", action="store_true", help="valores de ejemplo, sin consultar equipos")
    args = ap.parse_args(argv)
    global DEMO
    DEMO = args.demo
    registro.configurar()
    url = f"http://localhost:{args.puerto}"

    # 1) Tomar el puerto ANTES de sondear o tocar la base: si ya hay una
    #    instancia, esta se retira sin escribir nada (lección de un incidente anterior).
    try:
        servidor = Servidor((args.host, args.puerto), Manejador)
    except OSError:
        log.error("El puerto %d ya está en uso: el dashboard ya está corriendo "
                  "(o lo usa otro programa). No se inicia una segunda instancia.",
                  args.puerto)
        log.error("Abra %s en el navegador.", url)
        if not args.sin_navegador:
            webbrowser.open(url)
        return 2

    global MON
    try:
        if DEMO:
            from . import demo
            MON = monitor.MonitorClima(**demo.activar())
        else:
            MON = monitor.MonitorClima()
    except Exception:
        log.exception("No se pudo iniciar el monitor (revise config/datacenter.json)")
        servidor.server_close()
        return 1
    if args.host not in ("127.0.0.1", "localhost") and not MON.cfg.get("clave_ajustes"):
        log.error("Escuchar en %s sin 'clave_ajustes' dejaría los setpoints abiertos "
                  "a toda la red. Defina la clave en config/datacenter.json.", args.host)
        servidor.server_close()
        return 1

    borradas = MON.purgar()
    MON.ciclo()                                # primera lectura
    MON.arrancar()
    inst = MON.instantanea(publico=False)
    log.info("Clima Datacenter v%s · Python %s", __version__, sys.version.split()[0])
    log.info("sondas %s: %s", inst["sala"]["estacion"],
             "EN LINEA" if inst["comunicacion"] else "SIN COMUNICACION")
    fs = inst["fieldserver"]
    if fs:
        log.info("equipos de precisión %s: %s", fs["nombre"],
                 "EN LINEA" if fs["comunicacion"] else f"SIN COMUNICACION ({fs['error']})")
    for v in inst["variables"]:
        log.info("  %-24s %-12s %7s %-4s [%s]", v["sensor_nombre"], v["magnitud"],
                 v["valor"] if v["valor"] is not None else "—", v["unidad"], v["estado"])
    for zona, nombre in inst["zonas"].items():
        lim = MON.ajustes["zonas"][zona]
        log.info("setpoints %s: T %s–%s °C · HR %s–%s %%", nombre, lim["temp_baja"],
                 lim["temp_alta"], lim["hum_baja"], lim["hum_alta"])
    log.info("sondeo cada %ds, historial cada %ds, retención %d días%s", MON.intervalo,
             MON.guardar_cada, MON.retencion_dias,
             f" ({borradas} lecturas purgadas)" if borradas else "")
    log.info("dashboard en %s  (Ctrl+C para detener)", url)
    if not args.sin_navegador:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        log.info("Deteniendo…")
    finally:
        MON.detener()
        servidor.server_close()
    return 0
