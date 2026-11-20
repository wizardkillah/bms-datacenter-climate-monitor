"""Pruebas de la auditoria 2026-09-29: seguridad HTTP, datos sensibles,
instancia unica y correcciones del ciclo. Sin red externa (solo 127.0.0.1).

    python -m unittest discover tests
"""
import http.client
import json
import logging
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from clima import excel, fieldserver, monitor, servidor  # noqa: E402

logging.getLogger("clima").addHandler(logging.NullHandler())
logging.getLogger("clima").propagate = False

CONFIG = os.path.join(os.path.dirname(__file__), "..", "config", "datacenter.json")


def lecturas(dc=18.0, s2=18.0, el=19.0):
    d = {"s1.temperatura": el, "s1.humedad": 48, "s2.temperatura": s2, "s2.humedad": 50}
    for n in (30, 31, 32):
        d[f"p{n}.temperatura"], d[f"p{n}.humedad"] = dc, 55
    return d


class ConMonitor(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        cfg = os.path.join(self.dir, "dc.json")
        shutil.copy(CONFIG, cfg)
        self.mon = monitor.MonitorClima(cfg, os.path.join(self.dir, "aj.json"),
                                        os.path.join(self.dir, "c.db"))
        self.mon.fs_error = "read: <urlopen error http://192.0.2.75/json-rpc.jsn>"

    def tearDown(self):
        self.mon.bd.close()
        shutil.rmtree(self.dir, ignore_errors=True)


class DatosSensibles(ConMonitor):

    def test_api_sin_ip_mac_ni_errores(self):
        self.mon.ciclo(1_700_000_000, lecturas())
        self.mon.cambiar_ajustes({"sonido": False}, "10.1.2.3")
        self.mon.reconocer(quien="10.1.2.3")
        texto = json.dumps([self.mon.instantanea(), self.mon.eventos()], ensure_ascii=False)
        for sensible in ("192.0.2.", "00:00:5E", "10.1.2.3", "json-rpc", "urlopen"):
            self.assertNotIn(sensible, texto)

    def test_instantanea_interna_si_trae_ip(self):
        interna = self.mon.instantanea(publico=False)
        self.assertEqual(interna["sala"]["ip"], "192.0.2.38")

    def test_excel_sin_ip(self):
        import zipfile, io
        t = 1_700_000_000
        for i in range(5):
            self.mon.ciclo(t + i * 60, lecturas())
        datos, _ = excel.generar(self.mon, t, t + 600, 60)
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            contenido = "".join(z.read(n).decode("utf-8", "ignore") for n in z.namelist()
                                if n.endswith(".xml"))
        self.assertNotIn("192.0.2.", contenido)


class CicloBacnet(ConMonitor):

    def test_sonda_muda_no_salta_la_sana(self):
        """Dos None seguidos de la sonda del datacenter no deben dejar sin leer
        la sonda de la sala electrica si la estacion sigue viva."""
        leidos = []

        def leer(punto):
            leidos.append(punto["nombre_bacnet"])
            return None if punto["nombre_bacnet"].endswith(("232", "234")) else 18.5

        self.mon.fs = None
        self.mon._leer = leer
        self.mon._estacion_viva = lambda: True
        lect, _, _ = self.mon._leer_todo()
        self.assertEqual(lect["s1.temperatura"], 18.5)
        self.assertIn("TEMP_P3_231", leidos)

    def test_estacion_caida_no_espera_timeouts(self):
        self.mon.fs = None
        self.mon._leer = lambda punto: None
        self.mon._estacion_viva = lambda: False
        lect, _, _ = self.mon._leer_todo()
        self.assertTrue(all(v is None for k, v in lect.items() if k.startswith("s")))


class ExcelGrande(ConMonitor):

    def test_paso_se_ajusta_sobre_el_tope(self):
        viejo = excel.MAX_FILAS
        excel.MAX_FILAS = 10
        try:
            t = 1_700_000_000
            for i in range(30):
                self.mon.ciclo(t + i * 60, lecturas())
            self.assertGreater(excel.paso_efectivo(self.mon, t, t + 1800, 60), 60)
        finally:
            excel.MAX_FILAS = viejo


class FieldServerSoloLectura(unittest.TestCase):

    def test_metodo_de_escritura_rechazado_sin_red(self):
        c = fieldserver.ClienteFieldServer("0.0.0.0")
        with self.assertRaises(fieldserver.ErrorFieldServer):
            c._rpc("write", Obj_ID="1_OID")


class ServidorHTTP(ConMonitor):

    def setUp(self):
        super().setUp()
        servidor.MON = self.mon
        self.mon.ciclo(1_700_000_000, lecturas())
        self.srv = servidor.Servidor(("127.0.0.1", 0), servidor.Manejador)
        self.puerto = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def pedir(self, metodo, ruta, cuerpo=None, cabeceras=None):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=5)
        c.request(metodo, ruta, body=cuerpo, headers=cabeceras or {})
        r = c.getresponse()
        datos = r.read()
        c.close()
        return r.status, datos, r

    def test_segunda_instancia_rechazada(self):
        with self.assertRaises(OSError):
            servidor.Servidor(("127.0.0.1", self.puerto), servidor.Manejador)

    def test_host_ajeno_rechazado(self):
        st, _, _ = self.pedir("GET", "/api/estado", cabeceras={"Host": "evil.example"})
        self.assertEqual(st, 403)
        st, _, _ = self.pedir("GET", "/api/estado")
        self.assertEqual(st, 200)

    def test_post_text_plain_rechazado(self):
        st, _, _ = self.pedir("POST", "/api/reconocer", "{}", {"Content-Type": "text/plain"})
        self.assertEqual(st, 415)

    def test_post_de_otro_origen_rechazado(self):
        st, _, _ = self.pedir("POST", "/api/reconocer", "{}",
                              {"Content-Type": "application/json",
                               "Origin": "http://evil.example"})
        self.assertEqual(st, 403)

    def test_post_valido(self):
        st, datos, _ = self.pedir("POST", "/api/reconocer", "{}",
                                  {"Content-Type": "application/json",
                                   "Origin": f"http://127.0.0.1:{self.puerto}"})
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(datos)["ok"])

    def test_json_no_objeto(self):
        st, _, _ = self.pedir("POST", "/api/ajustes", "[1,2]",
                              {"Content-Type": "application/json"})
        self.assertEqual(st, 400)

    def test_ruta_fuera_de_web(self):
        st, _, _ = self.pedir("GET", "/../config/datacenter.json")
        self.assertEqual(st, 404)
        st, _, _ = self.pedir("GET", "/%2e%2e/config/datacenter.json")
        self.assertEqual(st, 404)

    def test_cabeceras_de_seguridad(self):
        _, _, r = self.pedir("GET", "/")
        self.assertEqual(r.getheader("X-Frame-Options"), "DENY")
        self.assertIn("default-src 'self'", r.getheader("Content-Security-Policy"))
        self.assertNotIn("Python", r.getheader("Server"))

    def test_clave_de_ajustes(self):
        self.mon.cfg["clave_ajustes"] = "hov-2026"
        cab = {"Content-Type": "application/json"}
        st, _, _ = self.pedir("POST", "/api/ajustes", json.dumps({"sonido": True}), cab)
        self.assertEqual(st, 403)
        st, _, _ = self.pedir("POST", "/api/ajustes",
                              json.dumps({"sonido": True, "clave": "hov-2026"}), cab)
        self.assertEqual(st, 200)


if __name__ == "__main__":
    unittest.main()
