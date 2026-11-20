"""Registro a consola y a logs/clima.log con rotación (5 MB × 3)."""
import logging
import os
from logging.handlers import RotatingFileHandler

from . import DIR_LOGS


def configurar(nivel=logging.INFO):
    raiz = logging.getLogger("clima")
    if raiz.handlers:
        return raiz
    raiz.setLevel(nivel)
    formato = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                                "%Y-%m-%d %H:%M:%S")
    try:
        os.makedirs(DIR_LOGS, exist_ok=True)
        archivo = RotatingFileHandler(os.path.join(DIR_LOGS, "clima.log"),
                                      maxBytes=5 * 1024 * 1024, backupCount=3,
                                      encoding="utf-8")
        archivo.setFormatter(formato)
        raiz.addHandler(archivo)
    except OSError:
        pass                    # carpeta sin permiso de escritura: solo consola
    consola = logging.StreamHandler()
    consola.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    raiz.addHandler(consola)
    return raiz
