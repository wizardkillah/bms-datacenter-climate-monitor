"""Regresion 03-11-2026 (clima): una respuesta BACnet atrasada no debe
asignarse a otra lectura, ni la de otra IP a esta.

    python -m unittest discover tests
"""
import os
import socket
import struct
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from clima import bacnet  # noqa: E402


def respuesta(invoke, instancia):
    """ComplexACK de ReadProperty: cada AI 'vale' su numero de instancia."""
    apdu = (bytes([0x30, invoke, 0x0C, 0x0C]) + struct.pack(">I", instancia)
            + bytes([0x19, 85, 0x3E, 0x44]) + struct.pack(">f", float(instancia)) + b"\x3F")
    return bytes([0x81, 0x0A]) + struct.pack(">H", 6 + len(apdu)) + b"\x01\x00" + apdu


class EstacionFalsa:
    """Responde primero la respuesta ATRASADA de la peticion anterior."""

    def __init__(self, ip="127.0.0.1"):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((ip, 0))
        self.previa = None
        self.senuelo = None        # socket en otra IP que contesta primero
        threading.Thread(target=self._atender, daemon=True).start()

    def _atender(self):
        while True:
            try:
                data, cli = self.sock.recvfrom(1500)
            except OSError:
                return
            instancia = struct.unpack(">I", data[11:15])[0] & 0x3FFFFF
            if self.senuelo:       # mismo invoke-id, otro valor, otra IP
                self.senuelo.sendto(respuesta(data[8], 999), cli)
            if self.previa:
                self.sock.sendto(self.previa, cli)
            self.previa = respuesta(data[8], instancia)
            self.sock.sendto(self.previa, cli)


class Desfase(unittest.TestCase):

    def setUp(self):
        self.est = EstacionFalsa()
        self._puerto = bacnet.BACNET_PORT
        bacnet.BACNET_PORT = self.est.sock.getsockname()[1]
        self.c = bacnet.ClienteBacnet(timeout=1.0)

    def tearDown(self):
        bacnet.BACNET_PORT = self._puerto
        self.c.cerrar()
        self.est.sock.close()

    def test_cada_lectura_recibe_su_respuesta(self):
        for inst in (38, 40, 37, 39, 38, 40, 5, 6):
            self.assertEqual(self.c.leer("127.0.0.1", "analogInput", inst), float(inst))

    def test_respuesta_de_otra_ip_se_descarta(self):
        self.est.senuelo = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.est.senuelo.bind(("127.0.0.2", 0))
        try:
            for inst in (12, 13, 14):
                self.assertEqual(self.c.leer("127.0.0.1", "analogInput", inst), float(inst))
        finally:
            self.est.senuelo.close()
            self.est.senuelo = None

    def test_sin_respuesta_devuelve_none(self):
        self.est.sock.close()
        self.assertIsNone(self.c.leer("127.0.0.1", "analogInput", 1))


if __name__ == "__main__":
    unittest.main()