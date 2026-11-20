"""Punto de entrada: python -m clima [--puerto 8090] [--host 127.0.0.1] [--sin-navegador]"""
import sys

from .servidor import main

sys.exit(main())
