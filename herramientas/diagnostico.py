"""Diagnóstico del equipo: ¿puede este PC correr el dashboard?

    python herramientas\\diagnostico.py

Solo lee. Muestra Python, dependencias, permisos, puerto, comunicación BACnet
con la estación de sondas y con el FieldServer de los equipos de precisión.
Las IPs se muestran aquí (consola local) para que Informática pueda revisar
firewall y rutas; no se muestran en el dashboard.
"""
import json
import os
import socket
import sys
import time

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BASE)

OK, MAL, AVISO = "[ OK ]", "[FALLA]", "[AVISO]"
resultado = {"fallas": 0, "avisos": 0}


def linea(estado, texto):
    if estado == MAL:
        resultado["fallas"] += 1
    elif estado == AVISO:
        resultado["avisos"] += 1
    print(f"  {estado} {texto}")


def main():
    print("\nClima Datacenter - diagnóstico del equipo\n" + "=" * 44)

    v = sys.version_info
    linea(OK if v >= (3, 8) else MAL, f"Python {v.major}.{v.minor}.{v.micro} en {sys.executable}")

    try:
        import clima
        linea(OK, f"Paquete clima {clima.__version__} en {BASE}")
    except Exception as e:
        linea(MAL, f"No se pudo importar el paquete clima: {e!r}")
        return fin()

    try:
        import openpyxl
        linea(OK, f"openpyxl {openpyxl.__version__} (Excel) desde "
                  f"{'vendor' if 'vendor' in openpyxl.__file__ else 'instalación de Python'}")
    except Exception as e:
        linea(MAL, f"openpyxl no disponible, el Excel no funcionará: {e!r}")

    ruta = BASE.lower()
    for vigilada in ("downloads", "descargas", "desktop", "escritorio", "onedrive"):
        if vigilada in ruta:
            linea(AVISO, f"La carpeta está bajo '{vigilada}': el antivirus vigila esa ruta con más "
                         "agresividad. Instálelo en Documentos")
            break
    else:
        linea(OK, f"Carpeta de instalación: {BASE}")

    for d in (clima.DIR_DATOS, clima.DIR_LOGS, clima.DIR_CONFIG):
        try:
            os.makedirs(d, exist_ok=True)
            prueba = os.path.join(d, ".escritura")
            with open(prueba, "w") as f:
                f.write("ok")
            os.remove(prueba)
            linea(OK, f"Escritura en {os.path.relpath(d, BASE)}\\")
        except OSError as e:
            linea(MAL, f"Sin permiso de escritura en {d}: {e}")

    marcadas = zona_identifier()
    linea(OK if not marcadas else AVISO,
          "Sin marca de descarga (Zone.Identifier)" if not marcadas else
          f"{marcadas} archivo(s) con marca de descarga: haga doble clic en INSTALAR.py")

    s = socket.socket()
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind(("127.0.0.1", 8090))
        linea(OK, "Puerto 8090 libre")
    except OSError:
        linea(AVISO, "Puerto 8090 ocupado: el dashboard ya está corriendo u otro programa lo usa")
    finally:
        s.close()

    with open(os.path.join(clima.DIR_CONFIG, "datacenter.json"), encoding="utf-8") as f:
        cfg = json.load(f)

    from clima import bacnet, fieldserver
    ip = cfg["ip"]
    c = bacnet.ClienteBacnet(cfg.get("ip_local") or None, timeout=2.0)
    nombre = c.leer(ip, "device", cfg.get("device_id", 38), "object-name")
    if nombre:
        linea(OK, f"BACnet {ip}: responde '{nombre}'")
        for s_ in cfg["sensores"]:
            if s_.get("fuente", "bacnet") != "bacnet":
                continue
            for mag in ("temperatura", "humedad"):
                p = s_[mag]
                val = c.leer(ip, p["tipo"], p["instancia"])
                lo, hi = (10, 50) if mag == "temperatura" else (15, 95)
                if val is None:
                    linea(AVISO, f"  {s_['nombre']} {mag}: sin respuesta")
                elif not lo <= val <= hi:
                    linea(AVISO, f"  {s_['nombre']} {mag}: {val} (fuera de rango físico: "
                                 "sonda dañada o desconectada)")
                else:
                    linea(OK, f"  {s_['nombre']} {mag}: {val}")
    else:
        linea(MAL, f"BACnet {ip} (UDP 47808) no responde: revisar red, firewall de "
                   "Windows/el antivirus para python.exe y que el PC esté en la VLAN del BMS")
    c.cerrar()

    fs = cfg.get("fieldserver") or {}
    if fs.get("ip"):
        cli = fieldserver.ClienteFieldServer(fs["ip"], timeout=5)
        for n in sorted({s_["nodo"] for s_ in cfg["sensores"] if s_.get("nodo")}):
            t0 = time.time()
            try:
                eq = cli.leer_equipo(n)
                linea(OK, f"FieldServer {fs['ip']} nodo {n}: retorno {eq['retorno_t']} °C "
                          f"{eq['retorno_hr']} %HR ({time.time() - t0:.2f} s)")
            except Exception as e:
                linea(MAL, f"FieldServer {fs['ip']} nodo {n} (HTTP 80): {e}")
                break
    return fin()


def zona_identifier():
    """Cuenta archivos con el flujo Zone.Identifier (solo NTFS/Windows)."""
    if os.name != "nt":
        return 0
    n = 0
    for raiz, _, archivos in os.walk(BASE):
        for a in archivos:
            try:
                with open(os.path.join(raiz, a) + ":Zone.Identifier"):
                    n += 1
            except OSError:
                pass
    return n


def fin():
    print("=" * 44)
    if resultado["fallas"]:
        print(f"  {resultado['fallas']} falla(s), {resultado['avisos']} aviso(s). "
              "Revise docs\\INSTALACION_ESET.md")
        return 1
    print(f"  Listo para usar ({resultado['avisos']} aviso(s)). Inicie con INICIAR_CLIMA.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
