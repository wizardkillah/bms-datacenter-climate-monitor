"""Modo demostración: reemplaza los clientes de red por valores de ejemplo.

    python -m clima --demo

No abre sockets: nunca contacta un PLC ni un gateway. Usa su propia base (datos/demo.db).
"""
import math
import os
import random
import time

from . import DIR_DATOS, bacnet

# instancia de objeto analógico -> (valor base, amplitud de la oscilación diaria)
ANALOGICOS = {37: (24.0, 1.5), 38: (20.5, 0.8), 39: (45.0, 4.0), 40: (50.0, 3.0)}


def _onda(base, amp, inst):
    h = (time.time() % 86400) / 86400 * 2 * math.pi
    return round(base + amp * math.sin(h + inst) + random.uniform(-amp, amp) * 0.05, 2)


class ClienteDemo:
    def __init__(self, *args, **kwargs):
        pass

    def leer(self, ip, tipo, instancia, propiedad="present-value"):
        if tipo == "device" or propiedad != "present-value":
            return "DEMO"
        if tipo.startswith("analog"):
            base, amp = ANALOGICOS.get(instancia, (50.0, 1.0))
            return _onda(base, amp, instancia)
        return 0

    def cerrar(self):
        pass


class FieldServerDemo:
    """Equipos de precisión simulados con el mismo diccionario que decodifica fieldserver.py."""

    def __init__(self, *args, **kwargs):
        pass

    def leer_equipo(self, nodo):
        from .fieldserver import HORAS, OFFSETS, SALIDAS
        tipicos = {"retorno_t": 21.0, "retorno_hr": 48, "impulsion_t": 15.5, "impulsion_hr": 55,
                   "presion_aire": 120, "ventilador_pct": 70, "frio_pct": 45, "calor_pct": 0,
                   "sp_temp": 21.0, "sp_hr": 50, "offset_alarma_t_alta": 5.0,
                   "offset_alarma_t_baja": 5.0, "offset_alarma_hr_alta": 15,
                   "offset_alarma_hr_baja": 15, "limite_t_alta": 30.0, "limite_t_baja": 10.0,
                   "estado_unidad": 1}
        eq = {"nodo": nodo, "edad_datos_seg": 2.0}
        for clave in OFFSETS:
            v = tipicos.get(clave, 0)
            eq[clave] = _onda(v, 0.4, nodo) if isinstance(v, float) else v
        for nombre in SALIDAS.values():
            eq[nombre] = nombre in ("ventiladores", "compresor_1")
        for clave in HORAS:
            eq[clave] = 12000 + nodo * 731
        eq["alarmas"] = []
        eq["alarma_equipo_t_alta"] = round(eq["sp_temp"] + eq["offset_alarma_t_alta"], 1)
        return eq


def activar():
    bacnet.ClienteBacnet = ClienteDemo
    from . import fieldserver
    fieldserver.ClienteFieldServer = FieldServerDemo
    return {"ruta_bd": os.path.join(DIR_DATOS, "demo.db")}
