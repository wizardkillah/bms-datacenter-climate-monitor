"""Clima Datacenter — monitoreo de temperatura y humedad del datacenter y la
sala eléctrica del Hospital Demo.

Solo lectura: BACnet ReadProperty hacia la estación Sauter y métodos de
lectura JSON-RPC hacia el FieldServer de los equipos de precisión.
"""
import os
import sys

__version__ = "1.1.1"

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DIR_CONFIG = os.path.join(BASE, "config")
DIR_DATOS = os.path.join(BASE, "datos")
DIR_LOGS = os.path.join(BASE, "logs")
DIR_WEB = os.path.join(BASE, "web")
DIR_VENDOR = os.path.join(BASE, "vendor")

# Dependencias empaquetadas (openpyxl, et_xmlfile): el PC del hospital no
# necesita pip ni internet, y el antivirus no ve descargas en el arranque.
if os.path.isdir(DIR_VENDOR) and DIR_VENDOR not in sys.path:
    sys.path.insert(0, DIR_VENDOR)
