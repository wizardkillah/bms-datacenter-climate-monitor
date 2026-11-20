"""Monitor de clima: sondeo BACnet + FieldServer, alarmas, cruce y SQLite."""
import json
import logging
import math
import os
import sqlite3
import threading
import time
from datetime import datetime

from . import DIR_CONFIG, DIR_DATOS, alarmas, bacnet, fieldserver

CONFIG = os.path.join(DIR_CONFIG, "datacenter.json")
AJUSTES = os.path.join(DIR_CONFIG, "ajustes.json")
BD = os.path.join(DIR_DATOS, "clima.db")

log = logging.getLogger("clima.monitor")

UNIDAD = {"temperatura": "°C", "humedad": "%HR"}

ESQUEMA = """
CREATE TABLE IF NOT EXISTS lecturas (
    epoch     INTEGER NOT NULL,
    variable  TEXT NOT NULL,
    valor     REAL,
    calidad   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_lecturas_epoch ON lecturas(epoch, variable);

CREATE TABLE IF NOT EXISTS eventos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    epoch       INTEGER NOT NULL,
    variable    TEXT,
    tipo        TEXT NOT NULL,
    anterior    TEXT,
    valor       REAL,
    limite      REAL,
    detalle     TEXT,
    reconocido  INTEGER,
    reconocido_epoch INTEGER,
    reconocido_por   TEXT
);
CREATE INDEX IF NOT EXISTS ix_eventos_epoch ON eventos(epoch DESC);
"""


def iso(epoch):
    return datetime.fromtimestamp(epoch).isoformat(sep=" ", timespec="seconds")


def _plano(aj):
    """Ajustes como claves planas (datacenter.temp_alta) para el registro."""
    plano = {}
    for k, v in aj.items():
        if k == "zonas":
            for z, lim in v.items():
                for kk, vv in lim.items():
                    plano[f"{z}.{kk}"] = vv
        else:
            plano[k] = v
    return plano


class MonitorClima:

    def __init__(self, ruta_config=CONFIG, ruta_ajustes=AJUSTES, ruta_bd=BD):
        with open(ruta_config, encoding="utf-8") as f:
            self.cfg = json.load(f)
        self.ruta_ajustes = ruta_ajustes
        self.ip = self.cfg["ip"]
        self.ip_local = self.cfg.get("ip_local") or None
        self.intervalo = int(self.cfg.get("intervalo_seg", 10))
        self.guardar_cada = int(self.cfg.get("guardar_cada_seg", 60))
        self.retencion_dias = int(self.cfg.get("retencion_dias", 730))

        self.ajustes = dict(alarmas.AJUSTES_DEFECTO)
        if os.path.exists(ruta_ajustes):
            with open(ruta_ajustes, encoding="utf-8") as f:
                leidos = json.load(f)
            self.ajustes = alarmas.validar_ajustes(leidos, self.ajustes)
            if leidos != self.ajustes:
                self._guardar_ajustes()         # formato antiguo o campos nuevos
        else:
            self._guardar_ajustes()

        self.variables = {}
        for s in self.cfg["sensores"]:
            for mag in ("temperatura", "humedad"):
                if mag in s:
                    clave = f"{s['id']}.{mag}"
                    self.variables[clave] = alarmas.Variable(
                        clave, s, mag, s[mag], UNIDAD[mag])

        self._lock = threading.RLock()
        self._parar = threading.Event()
        self._hilo = None
        self._cliente = None
        self._ultimo_guardado = 0
        self.ultimo_ciclo = None
        self.duracion_ciclo = None
        self.comunicacion = None        # True/False con la estacion Sauter
        self._aviso_bacnet = None
        # Equipos de precision (FieldServer)
        fs = self.cfg.get("fieldserver") or {}
        self.fs = (fieldserver.ClienteFieldServer(fs["ip"])
                   if fs.get("ip") and any(s.get("fuente") == "fieldserver"
                                            for s in self.cfg["sensores"]) else None)
        self.equipos = {}               # nodo -> ultimo dict decodificado
        self.fs_comunicacion = None
        self.fs_error = None
        self._alarmas_equipo = {}       # nodo -> set de alarmas internas vigentes
        self._discrepantes = set()      # variables que no calzan con los equipos
        self.cruce = {}

        os.makedirs(os.path.dirname(ruta_bd), exist_ok=True)
        self.bd = sqlite3.connect(ruta_bd, check_same_thread=False)
        self.bd.execute("PRAGMA journal_mode=WAL")
        self.bd.execute("PRAGMA synchronous=NORMAL")
        self.bd.executescript(ESQUEMA)
        self.bd.commit()
        self._restaurar_alarmas()

    # ---------- ajustes ----------

    def _guardar_ajustes(self):
        os.makedirs(os.path.dirname(self.ruta_ajustes), exist_ok=True)
        tmp = self.ruta_ajustes + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.ajustes, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.ruta_ajustes)

    def cambiar_ajustes(self, nuevos, origen=""):
        with self._lock:
            anteriores = self.ajustes
            validados = alarmas.validar_ajustes(nuevos, anteriores)
            antes, despues = _plano(anteriores), _plano(validados)
            cambios = [f"{k}: {antes.get(k)} → {v}"
                       for k, v in despues.items() if antes.get(k) != v]
            if not cambios:
                return validados
            self.ajustes = validados
            self._guardar_ajustes()
            self._evento(None, "ajuste", detalle="; ".join(cambios),
                         origen=origen)
            log.info("ajustes cambiados desde %s: %s", origen or "?", "; ".join(cambios))
            return validados

    def sensor_activo(self, sensor_id):
        return self.ajustes.get("sensores_activos", {}).get(sensor_id, True)

    # ---------- base de datos ----------

    def _evento(self, var, tipo, anterior=None, valor=None, limite=None,
                detalle=None, epoch=None, origen=None):
        reconocido = 0 if tipo in alarmas.ESTADOS_ALARMA else None
        # reconocido_por guarda el origen de ajustes y reconocimientos para
        # auditoria local; la API publica no lo expone.
        cur = self.bd.execute(
            "INSERT INTO eventos (epoch, variable, tipo, anterior, valor, limite,"
            " detalle, reconocido, reconocido_por) VALUES (?,?,?,?,?,?,?,?,?)",
            (int(epoch or time.time()), var, tipo, anterior, valor, limite,
             detalle, reconocido, origen))
        self.bd.commit()
        return cur.lastrowid

    def _restaurar_alarmas(self):
        """Tras un reinicio, recupera si la alarma vigente ya estaba reconocida.

        Sin esto, reiniciar el servicio volveria a hacer sonar una alarma que el
        operador ya habia reconocido.
        """
        estados = alarmas.ESTADOS_ALARMA + ("normal", "deshabilitado")
        marcas = ",".join("?" * len(estados))
        for clave, var in self.variables.items():
            fila = self.bd.execute(
                "SELECT id, tipo, reconocido FROM eventos WHERE variable=? "
                f"AND tipo IN ({marcas}) ORDER BY id DESC LIMIT 1",
                (clave, *estados)).fetchone()
            if fila and fila[1] in alarmas.ESTADOS_ALARMA:
                var._restaurado = (fila[0], fila[1], bool(fila[2]))
            fila = self.bd.execute(
                "SELECT tipo FROM eventos WHERE variable=? AND tipo IN "
                "('discrepancia','coincide') ORDER BY id DESC LIMIT 1",
                (clave,)).fetchone()
            if fila and fila[0] == "discrepancia":
                self._discrepantes.add(clave)

    # ---------- BACnet ----------

    def _cliente_bacnet(self):
        if self._cliente is None:
            self._cliente = bacnet.ClienteBacnet(self.ip_local, timeout=1.0)
        return self._cliente

    def _estacion_viva(self):
        inst = self.cfg.get("device_id", int(self.ip.split(".")[-1]))
        return self._cliente_bacnet().leer(self.ip, "device", inst, "object-name") is not None

    def _leer(self, punto):
        self._cliente_bacnet()
        v = self._cliente.leer(self.ip, punto["tipo"], punto["instancia"])
        if v is None:                                 # un reintento
            v = self._cliente.leer(self.ip, punto["tipo"], punto["instancia"])
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        if math.isnan(v) or math.isinf(v):
            return None
        return round(float(v), 2)

    # ---------- ciclo ----------

    def limite_de(self, var, estado):
        p = alarmas.PREFIJO[var.magnitud]
        lim = alarmas.limites(self.ajustes, var.zona)
        if estado in ("alta", "baja"):
            return lim[f"{p}_{estado}"]
        return None

    # Datos del FieldServer mas viejos que esto se tratan como sin comunicacion
    EDAD_MAX_FS = 90

    def _leer_equipos(self):
        nodos = sorted({s["nodo"] for s in self.cfg["sensores"]
                        if s.get("fuente") == "fieldserver"})
        equipos, error = {}, None
        for n in nodos:
            try:
                equipos[n] = self.fs.leer_equipo(n)
            except Exception as e:              # nunca perder el ciclo BACnet
                error = str(e)
                self.fs._oids = None            # re-resolver en el proximo ciclo
                if error != self.fs_error:
                    log.warning("FieldServer sin datos: %s", error)
                break
        return equipos, error

    def _leer_todo(self):
        lecturas, fallos, caida = {}, 0, False
        equipos, error = ({}, None) if self.fs is None else self._leer_equipos()
        for clave, var in self.variables.items():
            s = var.sensor
            if s.get("fuente") == "fieldserver":
                eq = equipos.get(s["nodo"])
                valido = eq and eq["edad_datos_seg"] <= self.EDAD_MAX_FS
                lecturas[clave] = eq.get(var.punto["campo"]) if valido else None
                continue
            if caida:
                lecturas[clave] = None
                continue
            lecturas[clave] = self._leer(var.punto)
            fallos = fallos + 1 if lecturas[clave] is None else 0
            if fallos >= 2:
                # Dos puntos seguidos sin respuesta: puede ser una sonda que no
                # contesta (la del datacenter lo hace) o la estacion caida. Solo
                # si la estacion tampoco responde se deja de esperar timeouts.
                caida = not self._estacion_viva()
                fallos = 0
        return lecturas, equipos, error

    def ciclo(self, ahora=None, lecturas=None):
        """Un sondeo completo. `lecturas` (clave->valor) permite probar sin red."""
        t0 = time.time()
        ahora = ahora or t0
        equipos, error = None, None
        if lecturas is None:
            lecturas, equipos, error = self._leer_todo()
        filas = []
        with self._lock:
            self.comunicacion = any(lecturas.get(k) is not None
                                    for k, v in self.variables.items()
                                    if v.sensor.get("fuente", "bacnet") == "bacnet")
            if self.comunicacion is False and self._aviso_bacnet is not False:
                log.warning("sin respuesta BACnet de %s", self.cfg.get("estacion"))
            elif self.comunicacion and self._aviso_bacnet is False:
                log.info("BACnet recuperado con %s", self.cfg.get("estacion"))
            self._aviso_bacnet = self.comunicacion
            if equipos is not None and self.fs is not None:
                if bool(equipos) and error is None and self.fs_comunicacion is False:
                    log.info("FieldServer recuperado")
                self.fs_comunicacion = bool(equipos) and error is None
                self.fs_error = error
                self.equipos = equipos
                self._revisar_alarmas_equipo(equipos, ahora)
            guardar = ahora - self._ultimo_guardado >= self.guardar_cada
            for clave, var in self.variables.items():
                valor = lecturas.get(clave)
                activo = self.sensor_activo(var.sensor["id"])
                cambio = var.actualizar(valor, self.ajustes, ahora, activo)
                if cambio:
                    self._registrar_cambio(var, *cambio, ahora=ahora)
                if guardar:
                    calidad = alarmas.clasificar(valor, var.magnitud, self.ajustes,
                                                 zona=var.zona)
                    calidad = calidad if calidad in ("falla_sensor",
                                                     "sin_comunicacion") else "ok"
                    filas.append((int(ahora), clave, valor, calidad))
            if filas:
                self.bd.executemany(
                    "INSERT INTO lecturas (epoch, variable, valor, calidad) "
                    "VALUES (?,?,?,?)", filas)
                self.bd.commit()
                self._ultimo_guardado = ahora
            self._cruzar(ahora)
            self.ultimo_ciclo = ahora
            self.duracion_ciclo = round(time.time() - t0, 2)

    def _revisar_alarmas_equipo(self, equipos, ahora):
        """Registra como evento cada alarma interna que aparece o se despeja."""
        for nodo, eq in equipos.items():
            nuevas = set(eq.get("alarmas", []))
            previas = self._alarmas_equipo.get(nodo)
            self._alarmas_equipo[nodo] = nuevas
            if previas is None:
                previas = self._alarmas_equipo_restauradas(nodo)
            nombre = self._nombre_equipo(nodo)
            for a in sorted(nuevas - previas):
                self._evento(f"equipo.{nodo}", "alarma_equipo",
                             detalle=f"{nombre}: alarma interna {a}", epoch=ahora)
            for a in sorted(previas - nuevas):
                self._evento(f"equipo.{nodo}", "equipo_normal",
                             detalle=f"{nombre}: se despejó la alarma interna {a}",
                             epoch=ahora)

    def _alarmas_equipo_restauradas(self, nodo):
        """Alarmas internas abiertas segun el registro, para no duplicarlas."""
        abiertas = set()
        for tipo, det in self.bd.execute(
                "SELECT tipo, detalle FROM eventos WHERE variable=? ORDER BY id",
                (f"equipo.{nodo}",)):
            codigo = det.rsplit(" ", 1)[-1]
            (abiertas.add if tipo == "alarma_equipo" else abiertas.discard)(codigo)
        return abiertas

    def _nombre_equipo(self, nodo):
        return next((s["nombre"] for s in self.cfg["sensores"]
                     if s.get("nodo") == nodo), f"Equipo {nodo}")

    def _cruzar(self, ahora):
        """Compara las sondas de sala con el retorno de los equipos de precision.

        El aire de retorno de los equipos es la mejor referencia de la
        temperatura de la sala: si una sonda de la misma zona se aleja del
        promedio mas que `discrepancia_*`, se marca como discrepante.
        """
        validos = ("normal", "alta", "baja")
        cruce = {}
        for zona in (self.cfg.get("zonas") or {"datacenter": ""}):
            vars_z = [v for v in self.variables.values() if v.zona == zona]
            if not vars_z:
                continue
            r = {"zona": zona}
            for mag, p in (("temperatura", "temp"), ("humedad", "hum")):
                ref = [v.valor for v in vars_z if v.magnitud == mag
                       and v.sensor.get("fuente") == "fieldserver" and v.estado in validos]
                todos = [v.valor for v in vars_z
                         if v.magnitud == mag and v.estado in validos]
                r[f"ref_{p}"] = round(sum(ref) / len(ref), 1) if ref else None
                r[f"max_{p}"] = max(todos) if todos else None
                r[f"min_{p}"] = min(todos) if todos else None
                r[f"prom_{p}"] = round(sum(todos) / len(todos), 1) if todos else None
                r[f"n_{p}"] = len(todos)
            sondas = {}
            for v in vars_z:
                if v.sensor.get("fuente") == "fieldserver":
                    continue
                p = alarmas.PREFIJO[v.magnitud]
                ref = r[f"ref_{p}"]
                d = sondas.setdefault(v.sensor["id"], {"nombre": v.sensor["nombre"]})
                if ref is None:
                    d[f"dif_{p}"], d[f"estado_{p}"] = None, "sin_referencia"
                    continue
                if v.estado not in validos:
                    d[f"dif_{p}"], d[f"estado_{p}"] = None, "sonda_invalida"
                    continue
                dif = round(v.valor - ref, 1)
                ok = abs(dif) <= self.ajustes[f"discrepancia_{p}"]
                d[f"dif_{p}"], d[f"estado_{p}"] = dif, ("coincide" if ok else "discrepante")
                if not ok and v.clave not in self._discrepantes:
                    self._discrepantes.add(v.clave)
                    self._evento(v.clave, "discrepancia", valor=v.valor, limite=ref,
                                 detalle=f"{v.sensor['nombre']} {v.magnitud} difiere "
                                         f"{dif:+} del retorno de los equipos ({ref})",
                                 epoch=ahora)
                elif ok and v.clave in self._discrepantes:
                    self._discrepantes.discard(v.clave)
                    self._evento(v.clave, "coincide", valor=v.valor, limite=ref,
                                 detalle=f"{v.sensor['nombre']} {v.magnitud} vuelve a "
                                         f"coincidir con los equipos", epoch=ahora)
            r["sondas"] = sondas
            cruce[zona] = r
        self.cruce = cruce

    def _registrar_cambio(self, var, anterior, nuevo, ahora):
        restaurado = getattr(var, "_restaurado", None)
        var._restaurado = None
        if anterior == "iniciando":
            # Arranque: si sigue la misma alarma que habia antes de reiniciar,
            # se continua ese evento en vez de abrir uno nuevo.
            if restaurado and restaurado[1] == nuevo:
                var.evento_id, var.reconocida = restaurado[0], restaurado[2]
                return
            if restaurado:
                anterior = restaurado[1]      # cerrar la alarma que quedo abierta
            elif nuevo == "normal":
                return
        tipo = nuevo
        nombre = f"{var.sensor['nombre']} · {var.magnitud}"
        detalle = {
            "alta": f"{nombre} sobre el máximo",
            "baja": f"{nombre} bajo el mínimo",
            "falla_sensor": f"{nombre}: lectura fuera de rango físico ({var.valor})",
            "sin_comunicacion": f"{nombre}: sin respuesta BACnet de {self.cfg['estacion']}",
            "normal": f"{nombre} normalizado",
            "deshabilitado": f"{nombre} fuera de servicio (deshabilitado)",
        }.get(nuevo, nombre)
        eid = self._evento(var.clave, tipo, anterior, var.valor,
                           self.limite_de(var, nuevo), detalle, ahora)
        if nuevo in alarmas.ESTADOS_ALARMA:
            var.evento_id = eid
        else:
            var.evento_id = None
            var.reconocida = True

    # ---------- reconocimiento ----------

    def reconocer(self, clave=None, quien=""):
        """Reconoce una alarma (o todas si clave es None). Devuelve cuantas."""
        n = 0
        with self._lock:
            for var in self.variables.values():
                if clave and var.clave != clave:
                    continue
                if var.en_alarma and not var.reconocida:
                    var.reconocida = True
                    n += 1
                    if var.evento_id:
                        self.bd.execute(
                            "UPDATE eventos SET reconocido=1, reconocido_epoch=?,"
                            " reconocido_por=? WHERE id=?",
                            (int(time.time()), quien, var.evento_id))
            self.bd.commit()
        return n

    # ---------- hilo ----------

    def arrancar(self):
        if self._hilo and self._hilo.is_alive():
            return
        self._parar.clear()
        self._hilo = threading.Thread(target=self._bucle, daemon=True,
                                      name="monitor-clima")
        self._hilo.start()

    def _bucle(self):
        ultimo_purgado = 0
        while not self._parar.is_set():
            try:
                self.ciclo()
                if time.time() - ultimo_purgado > 86400:
                    self.purgar()
                    ultimo_purgado = time.time()
            except Exception:                  # nunca matar el hilo de sondeo
                log.exception("error de ciclo")
            self._parar.wait(self.intervalo)

    def detener(self):
        self._parar.set()

    # ---------- consultas ----------

    def instantanea(self, publico=True):
        """Estado vivo. Con publico=True no incluye IP, MAC ni errores internos."""
        ahora = time.time()
        with self._lock:
            vars_ = []
            for var in self.variables.values():
                cand, cand_desde = var.pendiente()
                vars_.append({
                    "clave": var.clave,
                    "sensor": var.sensor["id"],
                    "sensor_nombre": var.sensor["nombre"],
                    "zona": var.zona,
                    "fuente": var.sensor.get("fuente", "bacnet"),
                    "magnitud": var.magnitud,
                    "unidad": var.unidad,
                    "nombre_bacnet": var.punto["nombre_bacnet"],
                    "objeto": (f"{var.punto['tipo']}:{var.punto['instancia']}"
                               if "tipo" in var.punto else
                               f"FieldServer nodo {var.sensor.get('nodo')}"),
                    "valor": var.valor,
                    "estado": var.estado,
                    "desde": iso(var.desde) if var.desde else None,
                    "reconocida": var.reconocida,
                    "pendiente": cand,
                    "pendiente_seg": round(ahora - cand_desde) if cand else None,
                    "limite": self.limite_de(var, var.estado),
                })
            activas = [v for v in vars_ if v["estado"] in alarmas.ESTADOS_ALARMA]
            return {
                "ts": iso(self.ultimo_ciclo) if self.ultimo_ciclo else None,
                "epoch": self.ultimo_ciclo,
                "duracion_ciclo": self.duracion_ciclo,
                "intervalo_seg": self.intervalo,
                "comunicacion": self.comunicacion,
                "sala": {k: self.cfg.get(k) for k in
                         (("sala", "ubicacion", "estacion", "modelo") if publico else
                          ("sala", "ubicacion", "estacion", "modelo", "ip", "mac"))},
                "sensores": [{"id": s["id"], "nombre": s["nombre"],
                              "zona": s.get("zona", "datacenter"),
                              "fuente": s.get("fuente", "bacnet"),
                              "nodo": s.get("nodo"),
                              "activo": self.sensor_activo(s["id"])}
                             for s in self.cfg["sensores"]],
                "zonas": self.cfg.get("zonas") or {"datacenter": "Sala datacenter"},
                "fieldserver": (self._resumen_fs(publico) if self.fs else None),
                "equipos": [self.equipos[n] for n in sorted(self.equipos)],
                "alarmas_equipo": sum(len(e.get("alarmas", []))
                                      for e in self.equipos.values()),
                "cruce": self.cruce,
                "variables": vars_,
                "alarmas_activas": len(activas),
                "sin_reconocer": sum(1 for v in activas if not v["reconocida"]),
                "ajustes": self.ajustes,
            }

    def _resumen_fs(self, publico):
        fs = self.cfg.get("fieldserver") or {}
        r = {"nombre": fs.get("nombre", "FieldServer"), "marca": fs.get("marca", ""),
             "comunicacion": self.fs_comunicacion}
        if not publico:
            r.update(ip=fs.get("ip"), error=self.fs_error)
        return r

    def historial(self, desde, hasta, max_puntos=720):
        """Series agregadas por intervalo (promedio, minimo, maximo)."""
        desde, hasta = int(desde), int(hasta)
        paso = max(self.guardar_cada,
                   math.ceil((hasta - desde) / max(1, max_puntos)))
        with self._lock:
            cur = self.bd.execute(
                "SELECT variable, (epoch / ?) * ? AS b, AVG(valor), MIN(valor),"
                " MAX(valor) FROM lecturas WHERE epoch BETWEEN ? AND ? "
                "AND calidad='ok' AND valor IS NOT NULL "
                "GROUP BY variable, b ORDER BY b", (paso, paso, desde, hasta))
            series = {}
            for var, b, prom, mn, mx in cur.fetchall():
                series.setdefault(var, []).append(
                    [b, round(prom, 2), round(mn, 2), round(mx, 2)])
            stats = {}
            for var, mn, mx, prom, n in self.bd.execute(
                    "SELECT variable, MIN(valor), MAX(valor), AVG(valor), COUNT(*)"
                    " FROM lecturas WHERE epoch BETWEEN ? AND ? AND calidad='ok'"
                    " AND valor IS NOT NULL GROUP BY variable", (desde, hasta)):
                stats[var] = {"min": round(mn, 2), "max": round(mx, 2),
                              "prom": round(prom, 2), "n": n}
        return {"desde": desde, "hasta": hasta, "paso_seg": paso,
                "series": series, "estadisticas": stats}

    def eventos(self, limite=200, desde=None, hasta=None, publico=True):
        sql = ("SELECT id, epoch, variable, tipo, anterior, valor, limite, detalle,"
               " reconocido, reconocido_epoch, reconocido_por FROM eventos")
        args = []
        if desde is not None:
            sql += " WHERE epoch BETWEEN ? AND ?"
            args += [int(desde), int(hasta)]
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limite))
        with self._lock:
            cur = self.bd.execute(sql, args)
            cols = [c[0] for c in cur.description]
            filas = [dict(zip(cols, f)) for f in cur.fetchall()]
        for f in filas:
            if publico:
                f.pop("reconocido_por", None)       # IP del equipo que reconocio
            f["ts"] = iso(f["epoch"])
            f["reconocido_ts"] = (iso(f["reconocido_epoch"])
                                  if f["reconocido_epoch"] else None)
        return filas

    def contar_filas_excel(self, desde, hasta, paso_seg):
        """Filas que tendria la hoja Lecturas (una por intervalo con datos)."""
        paso = max(1, int(paso_seg))
        with self._lock:
            return self.bd.execute(
                "SELECT COUNT(DISTINCT epoch / ?) FROM lecturas WHERE epoch BETWEEN ? AND ?",
                (paso, int(desde), int(hasta))).fetchone()[0]

    def lecturas_crudas(self, desde, hasta, paso_seg=60):
        """Tabla ancha epoch -> {variable: promedio} para el Excel."""
        paso = max(1, int(paso_seg))
        with self._lock:
            cur = self.bd.execute(
                "SELECT (epoch / ?) * ? AS b, variable, AVG(valor) FROM lecturas "
                "WHERE epoch BETWEEN ? AND ? AND calidad='ok' AND valor IS NOT NULL "
                "GROUP BY b, variable ORDER BY b", (paso, paso, int(desde), int(hasta)))
            tabla = {}
            for b, var, prom in cur.fetchall():
                tabla.setdefault(b, {})[var] = round(prom, 2)
        return tabla

    def purgar(self):
        limite = time.time() - self.retencion_dias * 86400
        with self._lock:
            cur = self.bd.execute("DELETE FROM lecturas WHERE epoch < ?", (limite,))
            self.bd.commit()
            if cur.rowcount:
                log.info("purga: %d lecturas anteriores a %d dias", cur.rowcount,
                         self.retencion_dias)
            return cur.rowcount
