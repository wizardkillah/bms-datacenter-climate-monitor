"""Preparación única en el PC del hospital (doble clic en INSTALAR.py).

- Quita la marca de descarga (Zone.Identifier) que dejan USB, correo o red.
- Crea datos\\ y logs\\.
- Corre las pruebas offline y el diagnóstico EN ESTE MISMO PROCESO (sin
  lanzar procesos hijos, patrón que el antivirus vigila).

No instala, no descarga, no pide administrador, no crea tareas programadas
ni reglas de firewall: esas decisiones son de Informática
(ver docs\\INSTALACION_ESET.md).
"""
import io
import os
import sys
import unittest

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BASE)


def quitar_zone_identifier():
    if os.name != "nt":
        return 0
    n = 0
    for raiz, _, archivos in os.walk(BASE):
        for a in archivos:
            try:
                os.remove(os.path.join(raiz, a) + ":Zone.Identifier")
                n += 1
            except OSError:
                pass
    return n


def correr_pruebas():
    import logging
    logging.getLogger("clima").addHandler(logging.NullHandler())
    logging.getLogger("clima").propagate = False
    carpeta = os.path.join(BASE, "tests")      # sin __init__.py: es su propio nivel superior
    suite = unittest.defaultTestLoader.discover(carpeta, top_level_dir=carpeta)
    salida = io.StringIO()
    r = unittest.TextTestRunner(stream=salida, verbosity=1).run(suite)
    if not r.wasSuccessful():
        print(salida.getvalue())
    return r.testsRun, len(r.failures) + len(r.errors)


def main():
    print("\nClima Datacenter - preparación del equipo\n" + "=" * 44)
    if sys.version_info < (3, 8):
        print("  [FALLA] Se requiere Python 3.8 o superior.")
        return 1
    n = quitar_zone_identifier()
    print(f"  [ OK ] Marca de descarga quitada de {n} archivo(s)")
    for d in ("datos", "logs"):
        os.makedirs(os.path.join(BASE, d), exist_ok=True)
    print("  [ OK ] Carpetas datos\\ y logs\\")

    print("\n  Pruebas offline (unos segundos)…", flush=True)
    total, fallas = correr_pruebas()
    print(f"  [ OK ] {total} pruebas aprobadas" if not fallas else
          f"  [FALLA] {fallas} de {total} pruebas fallaron: no usar hasta revisar")

    sys.path.insert(0, os.path.join(BASE, "herramientas"))
    import diagnostico
    d = diagnostico.main()
    if not fallas and not d:
        print("\n  Siguiente paso: doble clic en INICIAR_CLIMA.py")
    return 1 if (fallas or d) else 0


if __name__ == "__main__":
    sys.exit(main())
