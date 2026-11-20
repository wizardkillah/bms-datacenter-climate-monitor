"""Clima Datacenter — PREPARACIÓN (una sola vez). Doble clic en este archivo.

Quita la marca de descarga de los archivos, crea las carpetas, corre las
pruebas y el diagnóstico. No instala ni descarga nada y no pide administrador.
"""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
os.chdir(AQUI)
sys.path.insert(0, AQUI)
sys.path.insert(0, os.path.join(AQUI, "herramientas"))
sys.dont_write_bytecode = True

if __name__ == "__main__":
    from clima import consola
    consola.preparar("Clima Datacenter - preparación")
    try:
        import instalar
        codigo = instalar.main()
    except Exception:
        import traceback
        traceback.print_exc()
        codigo = 1
    consola.pausa()
    sys.exit(codigo)
