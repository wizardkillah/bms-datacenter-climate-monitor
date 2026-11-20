"""Clima Datacenter — ARRANQUE. Doble clic en este archivo.

Se abre una ventana de consola VISIBLE (a propósito: el antivirus marca los procesos
ocultos) y el navegador en http://localhost:8090. Cerrar la ventana detiene el
monitoreo. Si el dashboard ya está corriendo, esta ventana lo avisa y se
cierra sin tocar nada.

Sin .bat, sin PowerShell y sin procesos hijos: un único python.exe.
Si el doble clic abre un editor en vez de ejecutar: ver LEEME_PRIMERO.txt.
"""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
os.chdir(AQUI)
sys.path.insert(0, AQUI)
sys.dont_write_bytecode = True

if __name__ == "__main__":
    from clima import consola
    consola.preparar("Clima Datacenter - Hospital Demo")
    if sys.version_info < (3, 8):
        print("Se requiere Python 3.8 o superior. Versión actual:", sys.version.split()[0])
        consola.pausa()
        sys.exit(1)
    try:
        from clima.servidor import main
        codigo = main(sys.argv[1:])
    except Exception:
        import traceback
        traceback.print_exc()
        codigo = 1
    if codigo == 1:
        print("\nEl dashboard se detuvo con error. Revise logs\\clima.log o ejecute DIAGNOSTICO.py")
        consola.pausa()
    elif codigo == 2:
        consola.pausa("\nYa estaba corriendo. Presione Enter para cerrar esta ventana…")
    sys.exit(codigo)
