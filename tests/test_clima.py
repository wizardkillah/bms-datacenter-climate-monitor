"""Pruebas sin red: logica de alarmas, monitor con lecturas simuladas y Excel.

    python -m unittest discover tests
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from clima import alarmas, fieldserver, monitor  # noqa: E402

AJ = alarmas.validar_ajustes({}, alarmas.AJUSTES_DEFECTO)


class Clasificar(unittest.TestCase):

    def test_umbral_22_datacenter(self):
        self.assertEqual(alarmas.clasificar(22.0, "temperatura", AJ), "normal")
        self.assertEqual(alarmas.clasificar(22.1, "temperatura", AJ), "alta")

    def test_limites_por_zona(self):
        # 25 °C alarma en el datacenter pero no en la sala electrica (28)
        self.assertEqual(alarmas.clasificar(25, "temperatura", AJ, zona="datacenter"), "alta")
        self.assertEqual(alarmas.clasificar(25, "temperatura", AJ, zona="sala_electrica"),
                         "normal")

    def test_histeresis(self):
        self.assertEqual(alarmas.clasificar(21.8, "temperatura", AJ, "alta"), "alta")
        self.assertEqual(alarmas.clasificar(21.4, "temperatura", AJ, "alta"), "normal")

    def test_falla_y_comunicacion(self):
        self.assertEqual(alarmas.clasificar(0.0, "temperatura", AJ), "falla_sensor")
        self.assertEqual(alarmas.clasificar(6.3, "temperatura", AJ), "falla_sensor")
        self.assertEqual(alarmas.clasificar(12.4, "humedad", AJ), "falla_sensor")
        self.assertEqual(alarmas.clasificar(None, "humedad", AJ), "sin_comunicacion")


class Retardo(unittest.TestCase):

    def var(self):
        return alarmas.Variable("s2.temperatura", {"id": "s2", "nombre": "S2",
                                                   "zona": "datacenter"},
                                "temperatura", {}, "°C")

    def test_retardo_confirma(self):
        v = self.var()
        self.assertEqual(v.actualizar(20, AJ, 0), ("iniciando", "normal"))
        self.assertIsNone(v.actualizar(23, AJ, 10))
        self.assertIsNone(v.actualizar(23, AJ, 50))
        self.assertEqual(v.actualizar(23, AJ, 71), ("normal", "alta"))
        self.assertFalse(v.reconocida)

    def test_pico_aislado_no_alarma(self):
        v = self.var()
        v.actualizar(20, AJ, 0)
        v.actualizar(25, AJ, 10)
        v.actualizar(20, AJ, 20)
        self.assertIsNone(v.actualizar(25, AJ, 75))
        self.assertEqual(v.estado, "normal")

    def test_deshabilitado_inmediato(self):
        v = self.var()
        v.actualizar(0.0, AJ, 0)
        self.assertEqual(v.actualizar(0.0, AJ, 5, activo=False),
                         ("falla_sensor", "deshabilitado"))


class Validar(unittest.TestCase):

    def test_invertidos(self):
        with self.assertRaises(ValueError):
            alarmas.validar_ajustes(
                {"zonas": {"datacenter": {"temp_alta": 15, "temp_baja": 20}}}, AJ)

    def test_formato_antiguo_migra_a_datacenter(self):
        aj = alarmas.validar_ajustes({"temp_alta": "24", "hum_baja": 30}, AJ)
        self.assertEqual(aj["zonas"]["datacenter"]["temp_alta"], 24.0)
        self.assertEqual(aj["zonas"]["datacenter"]["hum_baja"], 30.0)
        self.assertNotIn("temp_alta", aj)

    def test_zona_desconocida(self):
        with self.assertRaises(ValueError):
            alarmas.validar_ajustes({"zonas": {"bodega": {"temp_alta": 30}}}, AJ)


class FieldServer(unittest.TestCase):

    def test_decodificacion(self):
        # Lectura real del nodo 31 (2026-09-29): compresor 1 + calefactor 1 en marcha
        mw01 = ["0"] * 210
        mw01[1], mw01[2], mw01[3], mw01[4], mw01[5] = "519", "57", "175", "-32764", "121"
        mw01[34], mw01[93], mw01[169], mw01[170], mw01[179] = "9200", "5", "180", "50", "100"
        mw01[43] = "4"
        mw02 = ["0"] * 32
        mw02[17] = "60319.000000"
        c = fieldserver.ClienteFieldServer("0.0.0.0")
        c._array = lambda n: ((mw01 if "MW01" in n else mw02), 2.0)
        eq = c.leer_equipo(31)
        self.assertEqual(eq["retorno_t"], 17.5)
        self.assertEqual(eq["retorno_hr"], 57)
        self.assertIsNone(eq["impulsion_hr"])          # -32764 = sin sonda
        self.assertTrue(eq["compresor_1"] and eq["calefactor_1"] and eq["ventiladores"])
        self.assertFalse(eq["compresor_2"])
        self.assertEqual(eq["ventilador_pct"], 92.0)
        self.assertEqual(eq["alarma_equipo_t_alta"], 28.0)
        self.assertEqual(eq["alarmas"], ["HR772.b2"])
        self.assertEqual(eq["horas_unidad"], 60319)


class Monitor(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        base = os.path.join(os.path.dirname(__file__), "..", "config", "datacenter.json")
        self.cfg = os.path.join(self.dir, "dc.json")
        shutil.copy(base, self.cfg)
        self.mon = monitor.MonitorClima(self.cfg, os.path.join(self.dir, "aj.json"),
                                        os.path.join(self.dir, "c.db"))

    def tearDown(self):
        self.mon.bd.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def lect(self, dc=18.0, s2=18.0, h2=50, el=19.0):
        d = {"s1.temperatura": el, "s1.humedad": 48, "s2.temperatura": s2,
             "s2.humedad": h2}
        for n in (30, 31, 32):
            d[f"p{n}.temperatura"], d[f"p{n}.humedad"] = dc, 55
        return d

    def test_alarma_y_reconocimiento(self):
        t = 1_700_000_000
        self.mon.ciclo(t, self.lect())
        for i in range(1, 10):
            self.mon.ciclo(t + i * 10, self.lect(dc=23.5, s2=23.5))
        inst = self.mon.instantanea()
        self.assertEqual(inst["sin_reconocer"], 4)      # 3 equipos + sonda
        altas = [e for e in self.mon.eventos() if e["tipo"] == "alta"]
        self.assertEqual(altas[0]["limite"], 22.0)
        self.assertEqual(self.mon.reconocer(quien="test"), 4)
        self.assertEqual(self.mon.instantanea()["sin_reconocer"], 0)

    def test_sala_electrica_usa_su_limite(self):
        t = 1_700_000_000
        for i in range(0, 10):
            self.mon.ciclo(t + i * 10, self.lect(el=25.0))
        est = {v["clave"]: v["estado"] for v in self.mon.instantanea()["variables"]}
        self.assertEqual(est["s1.temperatura"], "normal")

    def test_cruce_detecta_sonda_discrepante(self):
        t = 1_700_000_000
        self.mon.ciclo(t, self.lect(dc=18.0, s2=21.5))
        cruce = self.mon.instantanea()["cruce"]["datacenter"]
        self.assertEqual(cruce["ref_temp"], 18.0)
        self.assertEqual(cruce["sondas"]["s2"]["estado_temp"], "discrepante")
        self.assertEqual(self.mon.eventos()[0]["tipo"], "discrepancia")
        self.mon.ciclo(t + 10, self.lect(dc=18.0, s2=18.5))
        cruce = self.mon.instantanea()["cruce"]["datacenter"]
        self.assertEqual(cruce["sondas"]["s2"]["estado_temp"], "coincide")

    def test_sonda_en_falla_no_se_compara(self):
        self.mon.ciclo(1_700_000_000, self.lect(s2=0.0, h2=0.0))
        s2 = self.mon.instantanea()["cruce"]["datacenter"]["sondas"]["s2"]
        self.assertEqual(s2["estado_temp"], "sonda_invalida")

    def test_historial_y_excel(self):
        t = 1_700_000_000
        for i in range(0, 180):
            self.mon.ciclo(t + i * 60, self.lect(dc=18 + (i % 10) / 2, h2=0.0))
        h = self.mon.historial(t, t + 180 * 60)
        self.assertIn("p30.temperatura", h["series"])
        self.assertNotIn("s2.humedad", h["series"])        # 0 %HR = falla
        from clima import excel
        datos, nombre = excel.generar(self.mon, t, t + 180 * 60, 300)
        self.assertTrue(datos.startswith(b"PK"))

    def test_ajustes_persisten(self):
        self.mon.cambiar_ajustes({"zonas": {"datacenter": {"temp_alta": 24}}}, "test")
        with open(os.path.join(self.dir, "aj.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["zonas"]["datacenter"]["temp_alta"], 24.0)
        ev = self.mon.eventos()[0]
        self.assertEqual(ev["tipo"], "ajuste")
        self.assertIn("datacenter.temp_alta: 22.0 → 24.0", ev["detalle"])


if __name__ == "__main__":
    unittest.main()
