"""Genera el Excel de descarga con lecturas, estadisticas, eventos y graficos."""
import io
import os
from datetime import datetime

from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import DIR_WEB
from .alarmas import limites

LOGO_PNG = os.path.join(DIR_WEB, "img", "logo_hospital.png")

# Tope de filas de la hoja Lecturas. Un año a 1 minuto son 525 mil filas: el
# archivo tarda minutos y congela el equipo. Sobre el tope se sube la
# resolucion automaticamente y se deja constancia en el Resumen.
MAX_FILAS = 50_000
PASOS = (60, 300, 900, 1800, 3600, 3 * 3600, 6 * 3600, 86400)

AZUL = "1F3B5C"
NARANJO = "F9A825"
ROJO = PatternFill("solid", fgColor="F8CBCB")
CELESTE = PatternFill("solid", fgColor="CFE2F3")
CABECERA = PatternFill("solid", fgColor=AZUL)
BLANCO = Font(color="FFFFFF", bold=True)
FINO = Side(style="thin", color="BFC7D5")
BORDE = Border(left=FINO, right=FINO, top=FINO, bottom=FINO)

NOMBRE_TIPO = {
    "alta": "ALARMA ALTA", "baja": "ALARMA BAJA",
    "falla_sensor": "FALLA SENSOR", "sin_comunicacion": "SIN COMUNICACIÓN",
    "normal": "Normalizado", "deshabilitado": "Deshabilitado", "ajuste": "Ajuste",
    "alarma_equipo": "ALARMA EQUIPO", "equipo_normal": "Equipo normalizado",
    "discrepancia": "DISCREPANCIA", "coincide": "Coincide con equipos",
}


def _logo(ws, celda="A1"):
    """Inserta el logo si Pillow esta disponible; si no, solo queda el texto."""
    try:
        from openpyxl.drawing.image import Image
        img = Image(LOGO_PNG)
        escala = 60 / img.height
        img.width, img.height = int(img.width * escala), 60
        ws.add_image(img, celda)
        return True
    except Exception:
        return False


def _cabecera(ws, fila, textos):
    for i, t in enumerate(textos, 1):
        c = ws.cell(row=fila, column=i, value=t)
        c.fill, c.font, c.border = CABECERA, BLANCO, BORDE
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def paso_efectivo(mon, desde, hasta, paso_seg):
    """Primer paso >= al pedido que deja la hoja bajo MAX_FILAS."""
    for paso in [p for p in PASOS if p >= paso_seg] or [PASOS[-1]]:
        if mon.contar_filas_excel(desde, hasta, paso) <= MAX_FILAS:
            return paso
    return PASOS[-1]


def _txt_paso(paso):
    if paso >= 3600:
        return f"{paso // 3600} h"
    return f"{paso // 60} min" if paso >= 60 else f"{paso} s"


def generar(mon, desde, hasta, paso_seg):
    """Devuelve (bytes, nombre_archivo)."""
    pedido = paso_seg
    paso_seg = paso_efectivo(mon, desde, hasta, paso_seg)
    inst = mon.instantanea()
    aj = inst["ajustes"]
    sala = inst["sala"]
    vars_ = inst["variables"]
    tabla = mon.lecturas_crudas(desde, hasta, paso_seg)
    hist = mon.historial(desde, hasta)
    eventos = mon.eventos(100000, desde, hasta)

    wb = Workbook()

    # ---------- Resumen ----------
    ws = wb.active
    ws.title = "Resumen"
    con_logo = _logo(ws, "A1")
    fila0 = 5 if con_logo else 1
    ws.cell(row=fila0, column=1,
            value=f"Clima {sala['sala']} — Hospital Demo").font = Font(
        size=15, bold=True, color=AZUL)
    info = [
        ("Estación", f"{sala['estacion']} ({sala['modelo']})"),
        ("Ubicación", sala["ubicacion"]),
        ("Período", f"{datetime.fromtimestamp(desde):%d-%m-%Y %H:%M} a "
                    f"{datetime.fromtimestamp(hasta):%d-%m-%Y %H:%M}"),
        ("Resolución", _txt_paso(paso_seg) + (
            f" (se pidió {_txt_paso(pedido)}; ajustada para no superar "
            f"{MAX_FILAS:,} filas)".replace(",", ".") if paso_seg != pedido else "")),
        ("Generado", f"{datetime.now():%d-%m-%Y %H:%M:%S}"),
    ]
    for zona, nombre_z in inst["zonas"].items():
        lim = limites(aj, zona)
        info.append((f"Setpoint {nombre_z}",
                     f"{lim['temp_baja']} a {lim['temp_alta']} °C · "
                     f"{lim['hum_baja']} a {lim['hum_alta']} %HR"))
    info += [
        ("Histéresis", f"{aj['histeresis_temp']} °C · {aj['histeresis_hum']} %HR"),
        ("Retardo de alarma", f"{aj['retardo_seg']} s"),
        ("Referencia de sala", "Aire de retorno de los equipos de precisión "
                               "(FieldServer)"),
    ]
    f = fila0 + 2
    for k, v in info:
        ws.cell(row=f, column=1, value=k).font = Font(bold=True)
        ws.cell(row=f, column=2, value=v)
        f += 1

    f += 1
    _cabecera(ws, f, ["Variable", "Origen", "Mínimo", "Promedio", "Máximo",
                      "Registros", "Minutos fuera de setpoint"])
    paso_min = mon.guardar_cada / 60
    for v in vars_:
        f += 1
        st = hist["estadisticas"].get(v["clave"], {})
        p = "temp" if v["magnitud"] == "temperatura" else "hum"
        lim = limites(aj, v["zona"])
        with mon._lock:
            fuera = mon.bd.execute(
                "SELECT COUNT(*) FROM lecturas WHERE variable=? AND epoch BETWEEN ? AND ?"
                " AND calidad='ok' AND (valor > ? OR valor < ?)",
                (v["clave"], desde, hasta, lim[f"{p}_alta"], lim[f"{p}_baja"])
            ).fetchone()[0]
        fila = [f"{v['sensor_nombre']} · {v['magnitud']} ({v['unidad']})",
                v["nombre_bacnet"], st.get("min"), st.get("prom"), st.get("max"),
                st.get("n", 0), round(fuera * paso_min)]
        for i, val in enumerate(fila, 1):
            c = ws.cell(row=f, column=i, value=val)
            c.border = BORDE
    f += 2
    ws.cell(row=f, column=1, value=(
        "Solo se consideran lecturas válidas: las marcadas como falla de sensor "
        "o sin comunicación quedan fuera de promedios y gráficos.")).font = Font(
        italic=True, color="666666")
    for col, ancho in zip("ABCDEFG", (34, 22, 11, 11, 11, 11, 16)):
        ws.column_dimensions[col].width = ancho

    # ---------- Lecturas ----------
    wl = wb.create_sheet("Lecturas")
    claves = [v["clave"] for v in vars_]
    titulos = ["Fecha y hora"] + [
        f"{v['sensor_nombre']} {'Temp.' if v['magnitud'] == 'temperatura' else 'Humedad'}"
        f" ({v['unidad']})"
        for v in vars_]
    _cabecera(wl, 1, titulos)
    wl.freeze_panes = "B2"
    fila = 1
    for b in sorted(tabla):
        fila += 1
        wl.cell(row=fila, column=1, value=datetime.fromtimestamp(b)).number_format = \
            "dd-mm-yyyy hh:mm"
        for i, k in enumerate(claves, 2):
            val = tabla[b].get(k)
            if val is not None:
                wl.cell(row=fila, column=i, value=val).number_format = "0.0"
    wl.column_dimensions["A"].width = 18
    for i in range(2, len(claves) + 2):
        wl.column_dimensions[get_column_letter(i)].width = 17
    ultima = max(fila, 2)
    for i, v in enumerate(vars_, 2):
        p = "temp" if v["magnitud"] == "temperatura" else "hum"
        lim = limites(aj, v["zona"])
        rango = f"{get_column_letter(i)}2:{get_column_letter(i)}{ultima}"
        wl.conditional_formatting.add(rango, CellIsRule(
            operator="greaterThan", formula=[str(lim[f"{p}_alta"])], fill=ROJO))
        wl.conditional_formatting.add(rango, CellIsRule(
            operator="lessThan", formula=[str(lim[f"{p}_baja"])], fill=CELESTE))

    # ---------- Graficos ----------
    if fila >= 3:
        wg = wb.create_sheet("Gráficos")
        fechas = Reference(wl, min_col=1, min_row=2, max_row=fila)
        pos = 1
        graficos = [(z, mag, f"{'Temperatura' if mag == 'temperatura' else 'Humedad'} "
                             f"· {nz}", "°C" if mag == "temperatura" else "%HR")
                    for z, nz in inst["zonas"].items()
                    for mag in ("temperatura", "humedad")]
        for zona, mag, titulo, eje in graficos:
            ch = LineChart()
            ch.title, ch.y_axis.title, ch.x_axis.title = titulo, eje, "Fecha"
            ch.height, ch.width = 9, 26
            ch.x_axis.number_format = "dd-mm hh:mm"
            for i, v in enumerate(vars_, 2):
                if v["magnitud"] != mag or v["zona"] != zona:
                    continue
                ch.add_data(Reference(wl, min_col=i, min_row=1, max_row=fila),
                            titles_from_data=True)
            for s in ch.series:
                s.smooth = False
                s.graphicalProperties.line.width = 15000
            ch.set_categories(fechas)
            wg.add_chart(ch, f"A{pos}")
            pos += 20

    # ---------- Equipos de precision (foto al momento de generar) ----------
    if inst.get("equipos"):
        wq = wb.create_sheet("Equipos precisión")
        wq.cell(row=1, column=1, value=(
            f"Equipos de precisión — lectura del {datetime.now():%d-%m-%Y %H:%M}")).font = \
            Font(size=13, bold=True, color=AZUL)
        filas_eq = [
            ("Temperatura retorno (°C)", "retorno_t"), ("Humedad retorno (%HR)", "retorno_hr"),
            ("Temperatura impulsión (°C)", "impulsion_t"),
            ("Setpoint temperatura (°C)", "sp_temp"), ("Setpoint humedad (%HR)", "sp_hr"),
            ("Alarma alta del equipo (°C)", "alarma_equipo_t_alta"),
            ("Ventilador (%)", "ventilador_pct"), ("Compresor 1", "compresor_1"),
            ("Compresor 2", "compresor_2"), ("Calefactor etapa 1", "calefactor_1"),
            ("Calefactor etapa 2", "calefactor_2"), ("Estado unidad (código)", "estado_unidad"),
            ("Horas unidad", "horas_unidad"), ("Horas compresor 1", "horas_compresor_1"),
            ("Horas calefactor", "horas_calefactor"),
            ("Horas humidificador", "horas_humidificador"),
            ("Alarmas internas", "alarmas"),
        ]
        nombres_eq = {s["nodo"]: s["nombre"] for s in inst["sensores"] if s.get("nodo")}
        _cabecera(wq, 3, ["Dato"] + [nombres_eq.get(e["nodo"], f"Nodo {e['nodo']}")
                                     for e in inst["equipos"]])
        for r, (txt, k) in enumerate(filas_eq, 4):
            wq.cell(row=r, column=1, value=txt).font = Font(bold=True)
            for c, e in enumerate(inst["equipos"], 2):
                val = e.get(k)
                if isinstance(val, bool):
                    val = "Encendido" if val else "Apagado"
                elif isinstance(val, list):
                    val = ", ".join(val) or "Ninguna"
                wq.cell(row=r, column=c, value=val).border = BORDE
        wq.column_dimensions["A"].width = 30
        for c in range(2, len(inst["equipos"]) + 2):
            wq.column_dimensions[get_column_letter(c)].width = 22

    # ---------- Eventos ----------
    we = wb.create_sheet("Eventos")
    _cabecera(we, 1, ["Fecha y hora", "Tipo", "Variable", "Valor", "Límite",
                      "Detalle", "Reconocido"])
    nombres = {v["clave"]: f"{v['sensor_nombre']} · {v['magnitud']}" for v in vars_}
    nombres.update({f"equipo.{s['nodo']}": s["nombre"] for s in inst["sensores"]
                    if s.get("nodo")})
    for i, e in enumerate(reversed(eventos), 2):
        fila_e = [datetime.fromtimestamp(e["epoch"]), NOMBRE_TIPO.get(e["tipo"], e["tipo"]),
                  nombres.get(e["variable"], e["variable"] or "—"), e["valor"],
                  e["limite"], e["detalle"], e["reconocido_ts"] or ""]
        for j, val in enumerate(fila_e, 1):
            c = we.cell(row=i, column=j, value=val)
            if j == 1:
                c.number_format = "dd-mm-yyyy hh:mm:ss"
            if e["tipo"] in ("alta", "falla_sensor", "sin_comunicacion", "baja",
                             "alarma_equipo", "discrepancia"):
                c.font = Font(color="B00020", bold=(j == 2))
    for col, ancho in zip("ABCDEFGH", (20, 18, 26, 9, 9, 60, 20, 16)):
        we.column_dimensions[col].width = ancho

    buf = io.BytesIO()
    wb.save(buf)
    nombre = (f"clima_datacenter_{datetime.fromtimestamp(desde):%Y%m%d}"
              f"_{datetime.fromtimestamp(hasta):%Y%m%d}.xlsx")
    return buf.getvalue(), nombre
