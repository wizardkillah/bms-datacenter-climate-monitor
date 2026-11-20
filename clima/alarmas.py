"""Logica de alarmas del clima (sin E/S, para poder probarla).

Cada variable (sensor x magnitud) tiene un estado confirmado. Una lectura se
clasifica al instante y el estado confirmado solo cambia si la nueva
clasificacion se mantiene durante `retardo_seg`: asi un pico aislado no dispara
la alarma ni una lectura suelta la apaga. La histeresis evita el parpadeo
cuando la temperatura ronda justo el setpoint.

Los limites alto/bajo son por zona (sala datacenter, sala electrica); la
histeresis, el retardo y el rango fisico valido son comunes.
"""

# Clave corta de cada magnitud en los ajustes (temp_alta, hum_baja, ...)
PREFIJO = {"temperatura": "temp", "humedad": "hum"}

ESTADOS_ALARMA = ("alta", "baja", "falla_sensor", "sin_comunicacion")
LIMITES = ("temp_alta", "temp_baja", "hum_alta", "hum_baja")

ZONAS_DEFECTO = {
    # requerimiento: alarmar sobre 22 °C en el datacenter
    "datacenter": {"temp_alta": 22.0, "temp_baja": 15.0, "hum_alta": 60.0, "hum_baja": 40.0},
    # valores iniciales a confirmar con el hospital
    "sala_electrica": {"temp_alta": 28.0, "temp_baja": 10.0, "hum_alta": 80.0, "hum_baja": 20.0},
}

AJUSTES_DEFECTO = {
    "zonas": ZONAS_DEFECTO,
    "histeresis_temp": 0.5,
    "histeresis_hum": 2.0,
    "retardo_seg": 60,
    # Fuera de estos rangos la lectura no es fisica para la sala: sonda
    # desconectada o danada, no una condicion ambiental.
    "rango_valido_temp": [10.0, 50.0],
    "rango_valido_hum": [15.0, 95.0],
    # Diferencia maxima entre una sonda de sala y el promedio de los retornos
    # de los equipos de precision antes de marcarla como discrepante.
    "discrepancia_temp": 2.0,
    "discrepancia_hum": 10.0,
    "sonido": True,
    "sensores_activos": {},     # id -> False para dejar un sensor fuera de servicio
}


def limites(aj, zona):
    zonas = aj.get("zonas", {})
    return zonas.get(zona) or zonas.get("datacenter") or ZONAS_DEFECTO["datacenter"]


def clasificar(valor, magnitud, aj, estado_actual=None, zona="datacenter"):
    """Estado instantaneo de una lectura, con histeresis sobre el estado vigente."""
    if valor is None:
        return "sin_comunicacion"
    p = PREFIJO[magnitud]
    lo, hi = aj[f"rango_valido_{p}"]
    if not (lo <= valor <= hi):
        return "falla_sensor"
    lim = limites(aj, zona)
    alta, baja, h = lim[f"{p}_alta"], lim[f"{p}_baja"], aj[f"histeresis_{p}"]
    # Una vez en alarma, hay que bajar (o subir) la histeresis para salir
    if estado_actual == "alta" and valor > alta - h:
        return "alta"
    if estado_actual == "baja" and valor < baja + h:
        return "baja"
    if valor > alta:
        return "alta"
    if valor < baja:
        return "baja"
    return "normal"


class Variable:
    """Estado vivo de una variable con retardo de confirmacion."""

    def __init__(self, clave, sensor, magnitud, punto, unidad):
        self.clave = clave
        self.sensor = sensor            # dict del sensor en la config
        self.zona = sensor.get("zona", "datacenter")
        self.magnitud = magnitud
        self.punto = punto              # descripcion del origen del dato
        self.unidad = unidad
        self.valor = None
        self.estado = "iniciando"
        self.desde = None               # epoch del ultimo cambio confirmado
        self.candidato = None
        self.candidato_desde = None
        self.reconocida = True
        self.evento_id = None           # evento de la alarma vigente

    def actualizar(self, valor, aj, ahora, activo=True):
        """Procesa una lectura. Devuelve (anterior, nuevo) si cambio el estado."""
        self.valor = valor
        if not activo:
            inst = "deshabilitado"
        else:
            base = self.estado if self.estado in ESTADOS_ALARMA else None
            inst = clasificar(valor, self.magnitud, aj, base, self.zona)

        if inst == self.estado:
            self.candidato = self.candidato_desde = None
            return None

        # Arranque y (des)habilitacion se aplican de inmediato
        inmediato = (self.estado in ("iniciando", "deshabilitado")
                     or inst == "deshabilitado")
        if not inmediato:
            if self.candidato != inst:
                self.candidato, self.candidato_desde = inst, ahora
                return None
            if ahora - self.candidato_desde < aj["retardo_seg"]:
                return None

        anterior = self.estado
        self.estado, self.desde = inst, ahora
        self.candidato = self.candidato_desde = None
        self.reconocida = inst not in ESTADOS_ALARMA
        return anterior, inst

    @property
    def en_alarma(self):
        return self.estado in ESTADOS_ALARMA

    def pendiente(self):
        """Estado en espera de confirmacion y segundos transcurridos."""
        return self.candidato, self.candidato_desde


def _num(valor, clave):
    try:
        return float(valor)
    except (TypeError, ValueError):
        raise ValueError(f"{clave}: valor no numerico")


def validar_ajustes(nuevos, actuales):
    """Mezcla y valida ajustes recibidos del dashboard. Lanza ValueError."""
    aj = dict(actuales)
    zonas = {z: dict(v) for z, v in (actuales.get("zonas") or ZONAS_DEFECTO).items()}
    for z, v in ZONAS_DEFECTO.items():
        zonas.setdefault(z, dict(v))
    # Formato antiguo (limites en la raiz) = limites del datacenter
    for k in LIMITES:
        if k in nuevos:
            zonas["datacenter"][k] = _num(nuevos[k], k)
        aj.pop(k, None)
    for z, lim in (nuevos.get("zonas") or {}).items():
        if z not in zonas:
            raise ValueError(f"zona desconocida: {z}")
        for k in LIMITES:
            if k in lim:
                zonas[z][k] = _num(lim[k], f"{z}.{k}")
    aj["zonas"] = zonas

    for k in ("histeresis_temp", "histeresis_hum", "retardo_seg",
              "discrepancia_temp", "discrepancia_hum"):
        if k in nuevos:
            aj[k] = _num(nuevos[k], k)
    for k in ("rango_valido_temp", "rango_valido_hum"):
        if k in nuevos:
            try:
                lo, hi = (float(x) for x in nuevos[k])
            except (TypeError, ValueError):
                raise ValueError(f"{k}: se esperan dos numeros")
            if lo >= hi:
                raise ValueError(f"{k}: minimo debe ser menor que maximo")
            aj[k] = [lo, hi]
    if "sonido" in nuevos:
        aj["sonido"] = bool(nuevos["sonido"])
    if "sensores_activos" in nuevos:
        if not isinstance(nuevos["sensores_activos"], dict):
            raise ValueError("sensores_activos: se espera un objeto")
        aj["sensores_activos"] = {str(k): bool(v)
                                  for k, v in nuevos["sensores_activos"].items()}
    for k, v in AJUSTES_DEFECTO.items():
        aj.setdefault(k, v)

    aj["retardo_seg"] = int(aj["retardo_seg"])
    if not 0 <= aj["retardo_seg"] <= 3600:
        raise ValueError("retardo_seg: entre 0 y 3600 s")
    for z, lim in zonas.items():
        if lim["temp_baja"] >= lim["temp_alta"]:
            raise ValueError(f"{z}: la temperatura baja debe ser menor que la alta")
        if lim["hum_baja"] >= lim["hum_alta"]:
            raise ValueError(f"{z}: la humedad baja debe ser menor que la alta")
        if not (-20 <= lim["temp_baja"] and lim["temp_alta"] <= 60):
            raise ValueError(f"{z}: setpoints de temperatura fuera de rango (-20 a 60 °C)")
        if not (0 <= lim["hum_baja"] and lim["hum_alta"] <= 100):
            raise ValueError(f"{z}: setpoints de humedad fuera de rango (0 a 100 %HR)")
    for k in ("histeresis_temp", "histeresis_hum"):
        if not 0 <= aj[k] <= 10:
            raise ValueError(f"{k}: entre 0 y 10")
    for k in ("discrepancia_temp", "discrepancia_hum"):
        if not 0 < aj[k] <= 50:
            raise ValueError(f"{k}: entre 0 y 50")
    return aj
