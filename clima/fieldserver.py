"""Lectura de los equipos de precision (de precisión) via FieldServer.

El FieldServer FS-PRECISION (ProtoCessor FFP485; IP en config/datacenter.json) lee por
Modbus RTU los controladores de precisión (nodos 30, 31, 32) y deberia
publicarlos por BACnet/IP, pero su BACnet no responde (puerto configurado
65535). Se leen entonces sus data arrays con la API JSON-RPC de la propia
interfaz web, usando SOLO los metodos de lectura `read` y `read data array`.

Mapa de los arrays (perfil bacip_surveyevo.csv, MARCOM/ rev 1.36):
    DA_MW01_<n>  SInt16[210]  registros Modbus 257, 385, 513.. (ver OFFSETS)
    DA_MW02_<n>  Float[32]    horas de trabajo, caudales
    DA_MB01_<n>  Bit[160]     entradas/salidas digitales y alarmas
Valor -32764 = sonda no instalada.
"""
import json
import urllib.request

NO_DISPONIBLE = -32764

# offset en DA_MW01 -> (clave, escala)
OFFSETS = {
    "retorno_hr": (2, 1), "retorno_t": (3, 0.1),
    "impulsion_hr": (4, 1), "impulsion_t": (5, 0.1),
    "presion_aire": (6, 1),
    "estado_unidad": (93, 1),
    "ventilador_pct": (34, 0.01), "frio_pct": (35, 0.01), "calor_pct": (36, 0.01),
    "sp_temp": (169, 0.1), "sp_hr": (170, 1),
    "offset_alarma_t_alta": (179, 0.1), "offset_alarma_t_baja": (180, 0.1),
    "offset_alarma_hr_alta": (184, 1), "offset_alarma_hr_baja": (185, 1),
    "limite_t_alta": (186, 0.1), "limite_t_baja": (188, 0.1),
}
# bits de la palabra 1 (salidas digitales)
SALIDAS = {0: "ventiladores", 1: "compuerta", 2: "calefactor_1", 3: "calefactor_2",
           9: "compresor_1", 10: "compresor_2"}
# DA_MW02 (float)
HORAS = {"horas_unidad": 17, "horas_compresor_1": 4, "horas_compresor_2": 5,
         "horas_calefactor": 6, "horas_humidificador": 8}
# Palabras de alarma: DA_MW01 offsets 40..51 = registros Modbus 769..780
PALABRA_ALARMA_INI, PALABRA_ALARMA_FIN, REGISTRO_ALARMA = 40, 51, 769


# Solo estos metodos de la JSON-RPC se usan. La interfaz del FieldServer no
# tiene autenticacion y expone metodos que reconfiguran o reinician el equipo:
# cualquier otro metodo se rechaza aqui, antes de salir a la red.
METODOS_PERMITIDOS = frozenset({"read", "read data array"})

# Sin proxy: en el PC del hospital puede haber un proxy de sistema configurado
# que desviaria las peticiones a la red interna.
_ABRIDOR = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ErrorFieldServer(Exception):
    pass


class ClienteFieldServer:

    def __init__(self, ip, timeout=4.0):
        self.url = f"http://{ip}/json-rpc.jsn"
        self.timeout = timeout
        self._oids = None           # nombre de array -> Obj_ID de la vista de datos
        self._id = 0

    def _rpc(self, metodo, **params):
        if metodo not in METODOS_PERMITIDOS:
            raise ErrorFieldServer(f"metodo no permitido: {metodo}")
        self._id += 1
        params["protocol_version"] = 2
        cuerpo = json.dumps({"jsonrpc": "2.0", "method": metodo, "id": self._id,
                             "params": params}).encode()
        req = urllib.request.Request(self.url, cuerpo,
                                     {"Content-Type": "application/json"})
        try:
            with _ABRIDOR.open(req, timeout=self.timeout) as r:
                datos = json.loads(r.read())
        except Exception as e:
            raise ErrorFieldServer(f"{metodo}: {e}") from e
        if not isinstance(datos, dict):
            raise ErrorFieldServer(f"{metodo}: respuesta no valida")
        if "error" in datos:
            raise ErrorFieldServer(f"{metodo}: {datos['error'].get('message')}")
        return datos["result"]

    def _resolver(self):
        """Los Obj_ID dependen de la configuracion: se buscan por nombre."""
        lista = self._rpc("read", Obj_ID="40_OID")["Data"]["Children"]["Items"]
        oids = {}
        for item in lista:
            hijos = self._rpc("read", Obj_ID=item["ObjID"])["Data"]["Children"]["Items"]
            vista = next((h["ObjID"] for h in hijos if h["Name"] == "Data Array"), None)
            if vista:
                oids[item["Name"]] = vista
        self._oids = oids

    def _array(self, nombre):
        if self._oids is None or nombre not in self._oids:
            self._resolver()
        if nombre not in self._oids:
            raise ErrorFieldServer(f"array {nombre} no existe en el FieldServer")
        oid = self._oids[nombre]
        info = self._rpc("read", Obj_ID=oid)["Data"]["Display_Attributes"][
            "Data Array Attrib"]["Value"]
        datos = self._rpc("read data array", **{
            "Obj_ID": oid, "Display Mode": "D", "Start": "0",
            "Length": str(info["Length in Items"])})["Data"]["Display Data"]
        try:
            edad = float(str(info.get("Data Age", "0")).strip().rstrip("s") or 0)
        except ValueError:
            edad = float("inf")         # formato desconocido: se trata como dato viejo
        return datos, edad

    def leer_equipo(self, nodo):
        """Devuelve un dict decodificado del equipo, o lanza ErrorFieldServer."""
        try:
            return self._leer_equipo(nodo)
        except ErrorFieldServer:
            raise
        except (KeyError, IndexError, TypeError, ValueError) as e:
            # Respuesta con otra forma (firmware distinto, perfil cambiado):
            # se informa como falla del equipo, sin tumbar el ciclo completo.
            self._oids = None
            raise ErrorFieldServer(f"nodo {nodo}: formato inesperado ({e!r})") from e

    def _leer_equipo(self, nodo):
        mw01, edad = self._array(f"DA_MW01_{nodo}")
        mw02, _ = self._array(f"DA_MW02_{nodo}")
        w = [int(float(x)) for x in mw01]
        eq = {"nodo": nodo, "edad_datos_seg": round(edad, 1)}
        for clave, (off, esc) in OFFSETS.items():
            v = w[off]
            eq[clave] = None if v == NO_DISPONIBLE else round(v * esc, 2)
        eq["estado_unidad"] = w[93]
        for bit, nombre in SALIDAS.items():
            eq[nombre] = bool(w[1] >> bit & 1)
        for clave, off in HORAS.items():
            try:
                eq[clave] = int(float(mw02[off]))
            except (ValueError, IndexError):
                eq[clave] = None
        alarmas = []
        for off in range(PALABRA_ALARMA_INI, PALABRA_ALARMA_FIN + 1):
            palabra = w[off] & 0xFFFF
            for b in range(16):
                if palabra >> b & 1:
                    alarmas.append(f"HR{REGISTRO_ALARMA + off - PALABRA_ALARMA_INI}.b{b}")
        eq["alarmas"] = alarmas
        # Umbrales propios del equipo (setpoint + offset)
        if eq["sp_temp"] is not None and eq["offset_alarma_t_alta"] is not None:
            eq["alarma_equipo_t_alta"] = round(eq["sp_temp"] + eq["offset_alarma_t_alta"], 1)
        return eq
