"""Arma el paquete para el PC del hospital (se ejecuta en el equipo de desarrollo).

    python herramientas\\empaquetar.py [--con-historial] [--salida CARPETA]

Genera dist\\ClimaDatacenter_v<version>\\ y el zip
ClimaDatacenter_v<version>_hospital.zip (en --salida o en dist\\). Copia una
lista explícita de archivos; excluye base, logs, cachés y herramientas de
desarrollo. Se niega a empaquetar si aparece un .bat/.cmd/.ps1/.vbs/.pyw:
el antivirus bloquea esos scripts en el PC del hospital.
"""
import argparse
import hashlib
import os
import shutil
import sqlite3
import sys
import zipfile

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BASE)
import clima  # noqa: E402

INCLUIR = ["clima", "web", "config", "vendor", "herramientas", "tests", "docs",
           "INICIAR_CLIMA.py", "INSTALAR.py", "DIAGNOSTICO.py", "LEEME_PRIMERO.txt",
           "README.md", "CAMBIOS.md"]
IGNORAR = shutil.ignore_patterns("__pycache__", "*.pyc", "*.tmp", "*.log", "*.db",
                                 "*.db-shm", "*.db-wal", ".escritura", "empaquetar.py")
BLOQUEABLES = (".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".wsf", ".hta", ".pyw", ".lnk", ".exe")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--con-historial", action="store_true")
    ap.add_argument("--salida", help="carpeta donde dejar el .zip (por defecto dist\\)")
    args = ap.parse_args()

    nombre = f"ClimaDatacenter_v{clima.__version__}"
    dist = os.path.join(BASE, "dist")
    destino = os.path.join(dist, nombre)
    shutil.rmtree(destino, ignore_errors=True)
    os.makedirs(destino)
    for item in INCLUIR:
        src = os.path.join(BASE, item)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(destino, item), ignore=IGNORAR)
        elif os.path.isfile(src):
            shutil.copy2(src, destino)
        else:
            sys.exit(f"  falta {item}: no se arma un paquete incompleto")
    os.makedirs(os.path.join(destino, "datos"))
    os.makedirs(os.path.join(destino, "logs"))

    if args.con_historial:
        origen = os.path.join(BASE, "datos", "clima.db")
        # Copia consistente aun con el monitor corriendo (API de respaldo SQLite)
        with sqlite3.connect(origen) as a, sqlite3.connect(
                os.path.join(destino, "datos", "clima.db")) as b:
            a.backup(b)
        print("  historial incluido")

    bloqueables = [os.path.relpath(os.path.join(r, a), destino)
                   for r, _, fs in os.walk(destino) for a in fs
                   if a.lower().endswith(BLOQUEABLES)]
    if bloqueables:
        sys.exit(f"  paquete rechazado, contiene scripts que el antivirus bloquea: {bloqueables}")

    with open(os.path.join(destino, "SHA256SUMS.txt"), "w", encoding="utf-8") as f:
        for raiz, _, archivos in os.walk(destino):
            for a in sorted(archivos):
                if a == "SHA256SUMS.txt":
                    continue
                ruta = os.path.join(raiz, a)
                with open(ruta, "rb") as g:
                    h = hashlib.sha256(g.read()).hexdigest()
                f.write(f"{h}  {os.path.relpath(ruta, destino)}\n")

    salida = os.path.abspath(args.salida or dist)
    zipf = os.path.join(salida, f"{nombre}_hospital.zip")
    with zipfile.ZipFile(zipf, "w", zipfile.ZIP_DEFLATED) as z:
        for raiz, carpetas, archivos in os.walk(destino):
            rel = os.path.relpath(raiz, dist)
            if not archivos and not carpetas:
                z.writestr(rel.replace(os.sep, "/") + "/", "")   # datos\ y logs\ vacias
            for a in archivos:
                z.write(os.path.join(raiz, a), os.path.join(rel, a))
    print(f"  paquete: {destino}\n  zip:     {zipf} ({os.path.getsize(zipf) // 1024} kB)")


if __name__ == "__main__":
    main()
