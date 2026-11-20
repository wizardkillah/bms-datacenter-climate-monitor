"""Clima Datacenter — DIAGNÓSTICO. Doble clic en este archivo.

Revisa Python, dependencias, permisos, puerto y la comunicación con la
estación de sondas y con los equipos de precisión. Solo lee.
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
    consola.preparar("Clima Datacenter - diagnóstico")
    try:
        import diagnostico
        codigo = diagnostico.main()
    except Exception:
        import traceback
        traceback.print_exc()
        codigo = 1
    consola.pausa()
    sys.exit(codigo)
