"use strict";

// ---------------- utilidades ----------------
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const COLOR = { s2: "#c08cff", p30: "#4da3ff", p31: "#46d3d3", p32: "#ffd166", s1: "#ff9f6b" };
const PALETA = ["#4da3ff", "#c08cff", "#46d3d3", "#ffd166", "#ff9f6b"];
const colorSensor = (id, i) => COLOR[id] || PALETA[i % PALETA.length];
const ETIQ = {
  normal: "NORMAL", alta: "ALARMA ALTA", baja: "ALARMA BAJA",
  falla_sensor: "FALLA SENSOR", sin_comunicacion: "SIN COMUNICACIÓN",
  deshabilitado: "FUERA DE SERVICIO", iniciando: "INICIANDO", ajuste: "AJUSTE",
  alarma_equipo: "ALARMA EQUIPO", equipo_normal: "EQUIPO NORMAL",
  discrepancia: "DISCREPANCIA", coincide: "COINCIDE",
};
const ALARMAS = ["alta", "baja", "falla_sensor", "sin_comunicacion"];
const EVENTOS_ALARMA = [...ALARMAS, "alarma_equipo", "discrepancia"];
const fmt = (v, d = 1) => (v === null || v === undefined ? "—" : Number(v).toFixed(d).replace(".", ","));
const miles = (v) => (v === null || v === undefined ? "—" : Number(v).toLocaleString("es-CL"));
const pad = (n) => String(n).padStart(2, "0");
const hora = (t) => { const d = new Date(t * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const dia = (t) => { const d = new Date(t * 1000); return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}`; };
const fechaInput = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const guardado = (k, def) => { try { const v = localStorage.getItem(k); return v === null ? def : JSON.parse(v); } catch { return def; } };
const guardar = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* sin almacenamiento */ } };
const pref = (mag) => (mag === "temperatura" ? "temp" : "hum");
const lim = (zona) => (EST.ajustes.zonas[zona] || EST.ajustes.zonas.datacenter);

async function api(ruta, opciones) {
  const r = await fetch(ruta, opciones);
  const datos = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(datos.error || `HTTP ${r.status}`);
  return datos;
}

// ---------------- estado global ----------------
let EST = null;
let HIST = null;
let horas = guardado("clima.horas", 24);
let silenciado = guardado("clima.silencio", false);
let formEditado = false;
let eventosVistos = null;
let graficosCreados = false;

// ---------------- estado vivo ----------------
async function cargarEstado() {
  try {
    EST = await api("/api/estado");
    if (!graficosCreados) crearGraficos();
    pintarEstado();
  } catch (e) {
    $("#pulso").className = "pulso caido";
    banner("falla", "⚠", `Sin conexión con el servidor del dashboard (${e.message})`);
  }
}

function banner(clase, icono, texto) {
  const b = $("#banner");
  b.className = `banner banner-${clase}` + (EST && EST.sin_reconocer ? " sin-reconocer" : "");
  $("#banner-icono").textContent = icono;
  $("#banner-texto").textContent = texto;
}

function pintarEstado() {
  const e = EST;
  $("#ts").textContent = e.ts ? e.ts.slice(11) + " · " + e.ts.slice(8, 10) + "/" + e.ts.slice(5, 7) : "—";
  const p = $("#pulso");
  p.className = "pulso " + (e.comunicacion ? "ok" : "caido");
  void p.offsetWidth; if (e.comunicacion) p.classList.add("late");
  const fs = e.fieldserver;
  $("#pulso-fs").className = "pulso " + (!fs ? "oculto" : fs.comunicacion ? "ok" : "caido");
  $("#sub").textContent = `${e.sala.ubicacion} · sondas ${e.sala.estacion}` +
    (fs ? ` · ${e.equipos.length} equipos de precisión` : "");
  $("#pulso").title = `Sondas de sala (${e.sala.estacion}): ${e.comunicacion ? "en línea" : "sin comunicación"}`;
  if (fs) $("#pulso-fs").title = `Equipos de precisión: ${fs.comunicacion ? "en línea" : "sin comunicación"}`;
  $("#salud").textContent = `Sondeo cada ${e.intervalo_seg} s (último ciclo ${fmt(e.duracion_ciclo, 2)} s) · histórico cada minuto`;

  const activas = e.variables.filter((v) => ALARMAS.includes(v.estado));
  const criticas = activas.filter((v) => v.estado === "alta" || v.estado === "baja");
  const avisos = [];
  if (!e.comunicacion) avisos.push(`sin comunicación con ${e.sala.estacion}`);
  if (fs && !fs.comunicacion) avisos.push(`sin comunicación con ${fs.nombre}`);
  activas.filter((v) => !criticas.includes(v) && v.estado !== "sin_comunicacion")
    .forEach((v) => avisos.push(`${v.sensor_nombre} ${v.magnitud}: ${ETIQ[v.estado].toLowerCase()}`));
  e.equipos.filter((q) => q.alarmas.length).forEach((q) =>
    avisos.push(`${nombreEquipo(q.nodo)}: ${q.alarmas.length} alarma(s) interna(s)`));
  Object.values(e.cruce).forEach((c) => Object.values(c.sondas).forEach((s) => {
    if (s.estado_temp === "discrepante") avisos.push(`${s.nombre} difiere ${fmt(s.dif_temp)} °C de los equipos`);
    if (s.estado_hum === "discrepante") avisos.push(`${s.nombre} difiere ${fmt(s.dif_hum, 0)} %HR de los equipos`);
  }));

  if (criticas.length) {
    banner("alarma", "▲", "ALARMA DE CLIMA: " + criticas.map((v) =>
      `${v.sensor_nombre} ${v.magnitud} ${fmt(v.valor)} ${v.unidad} (${v.estado === "alta" ? "máx" : "mín"} ${fmt(v.limite)})`).join(" · ") +
      (avisos.length ? " — además: " + avisos.join(" · ") : ""));
  } else if (avisos.length) {
    banner("falla", "⚠", "Revisar: " + avisos.join(" · "));
  } else {
    const dc = e.cruce.datacenter;
    banner("ok", "●", "Clima dentro de setpoint" +
      (dc && dc.max_temp !== null ? ` · datacenter máx ${fmt(dc.max_temp)} °C (alarma sobre ${fmt(lim("datacenter").temp_alta)} °C)` : ""));
  }
  $("#btn-reconocer").classList.toggle("oculto", !e.sin_reconocer);
  $("#btn-reconocer").textContent = `Reconocer (${e.sin_reconocer})`;
  document.title = (criticas.length ? "▲ ALARMA · " : avisos.length ? "⚠ " : "") + "Clima Datacenter — Hospital Demo";

  pintarZonas();
  if (!formEditado) llenarAjustes();
  gestionarSonido();
  const firma = `${e.alarmas_activas}:${e.sin_reconocer}:${e.alarmas_equipo}:${avisos.length}`;
  if (eventosVistos !== null && firma !== eventosVistos) cargarEventos();
  eventosVistos = firma;
}

const nombreEquipo = (nodo) => (EST.sensores.find((s) => s.nodo === nodo) || {}).nombre || `Equipo ${nodo}`;

// ---------------- salas ----------------
function pintarZonas() {
  const e = EST;
  $("#zonas").innerHTML = Object.entries(e.zonas).map(([zona, nombre]) => {
    const sensores = e.sensores.filter((s) => s.zona === zona);
    if (!sensores.length) return "";
    const c = e.cruce[zona];
    return `<section class="zona">
      <div class="zona-cab"><h2>${esc(nombre)}</h2>
        <span class="zona-sp">Alarma ${fmt(lim(zona).temp_baja)}–${fmt(lim(zona).temp_alta)} °C · ${fmt(lim(zona).hum_baja, 0)}–${fmt(lim(zona).hum_alta, 0)} %HR</span></div>
      ${c && sensores.some((s) => s.fuente === "fieldserver") ? consolidado(zona, c) : ""}
      <div class="sensores">${sensores.map((s) => tarjeta(s, e.sensores.indexOf(s))).join("")}</div>
    </section>`;
  }).join("");
}

function consolidado(zona, c) {
  const l = lim(zona);
  const claseT = c.max_temp === null ? "e-sin_comunicacion" : c.max_temp > l.temp_alta ? "e-alta" : c.max_temp < l.temp_baja ? "e-baja" : "e-normal";
  const sondas = Object.values(c.sondas).map((s) => {
    const txt = (est, dif, u, d) => ({
      coincide: `<span class="ok-txt">coincide (${dif > 0 ? "+" : ""}${fmt(dif, d)} ${u})</span>`,
      discrepante: `<span class="mal-txt">difiere ${dif > 0 ? "+" : ""}${fmt(dif, d)} ${u}</span>`,
      sonda_invalida: `<span class="aviso-txt">sonda sin lectura válida</span>`,
      sin_referencia: `<span class="tenue">sin referencia de equipos</span>`,
    }[est] || "—");
    return `<div class="cruce-fila"><b>${esc(s.nombre)}</b> · temp. ${txt(s.estado_temp, s.dif_temp, "°C", 1)} · hum. ${txt(s.estado_hum, s.dif_hum, "%", 0)}</div>`;
  }).join("");
  return `<div class="consolidado ${claseT}">
    <div class="kpi"><span class="mag">Temperatura máx. sala</span><span class="val">${fmt(c.max_temp)}<small>°C</small></span>
      <span class="sp">prom ${fmt(c.prom_temp)} · mín ${fmt(c.min_temp)} · ${c.n_temp} lectura(s) válida(s)</span></div>
    <div class="kpi"><span class="mag">Humedad prom. sala</span><span class="val">${fmt(c.prom_hum, 0)}<small>%HR</small></span>
      <span class="sp">mín ${fmt(c.min_hum, 0)} · máx ${fmt(c.max_hum, 0)}</span></div>
    <div class="kpi"><span class="mag">Referencia equipos (retorno)</span><span class="val ref">${fmt(c.ref_temp)}<small>°C</small> ${fmt(c.ref_hum, 0)}<small>%HR</small></span>
      <span class="sp">promedio del aire de retorno de los equipos de precisión</span></div>
    <div class="cruce"><div class="mag">Cruce sondas de sala ↔ equipos</div>${sondas || "<span class='tenue'>sin sondas propias en esta sala</span>"}</div>
  </div>`;
}

function tarjeta(s, i) {
  const vs = EST.variables.filter((v) => v.sensor === s.id);
  const eq = s.fuente === "fieldserver" ? EST.equipos.find((q) => q.nodo === s.nodo) : null;
  const origen = s.fuente === "fieldserver"
    ? ` de precisión · nodo ${s.nodo}` : vs.map((v) => esc(v.nombre_bacnet)).join(" · ");
  return `<article class="sensor ${s.activo ? "" : "inactivo"} ${eq ? "equipo" : ""}" style="--c:${colorSensor(s.id, i)}">
    <div class="sensor-cab"><h3>${esc(s.nombre)}</h3><span class="obj">${origen}</span></div>
    <div class="lecturas">${vs.map((v) => lectura(v, eq)).join("")}</div>
    ${eq ? detalleEquipo(eq) : s.fuente === "fieldserver" ? `<div class="detalle tenue">Sin datos del FieldServer</div>` : ""}
  </article>`;
}

function lectura(v, eq) {
  const p = pref(v.magnitud), l = lim(v.zona);
  const alta = l[`${p}_alta`], baja = l[`${p}_baja`];
  const lo = baja - (alta - baja) * 0.6, hi = alta + (alta - baja) * 0.6;
  const pos = (x) => Math.max(0, Math.min(100, ((x - lo) / (hi - lo)) * 100));
  const valido = v.valor !== null && !["falla_sensor", "sin_comunicacion", "deshabilitado"].includes(v.estado);
  const st = HIST && HIST.estadisticas[v.clave];
  const pend = v.pendiente && v.pendiente !== v.estado
    ? `<span class="pend">→ ${ETIQ[v.pendiente].toLowerCase()} en ${Math.max(0, EST.ajustes.retardo_seg - v.pendiente_seg)} s</span>` : "";
  return `<div class="lectura e-${v.estado} ${v.reconocida ? "" : "sin-rec"}">
    <div class="mag">${eq ? (v.magnitud === "temperatura" ? "Temp. retorno" : "Hum. retorno") : v.magnitud}</div>
    <div class="val">${v.estado === "deshabilitado" ? "—" : fmt(v.valor, v.magnitud === "humedad" && eq ? 0 : 1)}<small>${v.unidad}</small></div>
    <div class="barra-sp" style="--b:${pos(baja)}%;--a:${pos(alta)}%;--x:${pos(valido ? v.valor : lo)}%">${valido ? "<i></i>" : ""}</div>
    <span class="chip">${ETIQ[v.estado] || v.estado}</span>${pend}
    <div class="minmax">${st ? `${rotuloRango()}: ${fmt(st.min)} – ${fmt(st.max)} (prom ${fmt(st.prom)})` : `${rotuloRango()}: sin datos válidos`}</div>
  </div>`;
}

function detalleEquipo(q) {
  const on = (b, txt) => `<span class="io ${b ? "on" : ""}">${txt}</span>`;
  const alarmas = q.alarmas.length
    ? `<span class="mal-txt">${q.alarmas.length} activa(s): ${q.alarmas.map(esc).join(", ")}</span>` : `<span class="ok-txt">ninguna</span>`;
  return `<div class="detalle">
    <div class="d-fila"><span>Impulsión</span><b>${fmt(q.impulsion_t)} °C</b><span>Setpoint equipo</span><b>${fmt(q.sp_temp)} °C · ${fmt(q.sp_hr, 0)} %</b></div>
    <div class="d-fila"><span>Ventilador</span><b>${fmt(q.ventilador_pct, 0)} %</b><span>Alarma propia a</span><b>${fmt(q.alarma_equipo_t_alta)} °C</b></div>
    <div class="io-fila">${on(q.ventiladores, "Ventiladores")}${on(q.compresor_1, "Compresor 1")}${on(q.compresor_2, "Compresor 2")}${on(q.calefactor_1, "Calefactor 1")}${on(q.calefactor_2, "Calefactor 2")}</div>
    <div class="d-fila"><span>Alarmas internas</span><b>${alarmas}</b></div>
    <div class="d-pie">Estado unidad cód. ${q.estado_unidad} · ${miles(q.horas_unidad)} h unidad · ${miles(q.horas_compresor_1)} h compresor 1 · dato de hace ${fmt(q.edad_datos_seg, 0)} s</div>
  </div>`;
}

const rotuloRango = () => (horas >= 24 ? `${horas / 24} d` : `${horas} h`);

// ---------------- historial y graficos ----------------
function crearGraficos() {
  const cont = $("#graficos");
  cont.innerHTML = Object.entries(EST.zonas).flatMap(([zona, nombre]) =>
    ["temperatura", "humedad"].map((mag) => `<div class="grafico-caja">
      <div class="grafico-titulo">${esc(nombre)} · ${mag === "temperatura" ? "Temperatura" : "Humedad relativa"} <span class="u">${mag === "temperatura" ? "°C" : "%HR"}</span></div>
      <canvas id="g-${zona}-${mag}" data-zona="${zona}" data-mag="${mag}" class="${zona === "datacenter" ? "" : "bajo"}"></canvas>
      <div class="leyenda" id="ley-${zona}-${mag}"></div></div>`)).join("");
  $$("canvas", cont).forEach(activarTooltip);
  graficosCreados = true;
}

async function cargarHistorial() {
  try {
    HIST = await api(`/api/historial?horas=${horas}`);
    dibujarTodo();
    pintarStats();
    if (EST) pintarZonas();
  } catch (e) { console.warn("historial", e); }
}

function seriesDe(zona, mag) {
  return EST.sensores.map((s, i) => ({ s, i })).filter(({ s }) => s.zona === zona).map(({ s, i }) => ({
    nombre: s.nombre, color: colorSensor(s.id, i), datos: HIST.series[`${s.id}.${mag}`] || [],
  }));
}

function dibujarTodo(cursor, cvCursor) {
  if (!EST || !HIST) return;
  $$("#graficos canvas").forEach((cv) => {
    const { zona, mag } = cv.dataset, l = lim(zona), p = pref(mag);
    const series = seriesDe(zona, mag);
    dibujar(cv, series, [
      { v: l[`${p}_alta`], color: "#ff4d5e", zona: "arriba" },
      { v: l[`${p}_baja`], color: "#4dc3ff", zona: "abajo" }], mag === "temperatura" ? "°C" : "%", cv === cvCursor ? cursor : undefined);
    const u = mag === "temperatura" ? "°C" : "%HR";
    $(`#ley-${zona}-${mag}`).innerHTML = series.map((s) =>
      `<span><i style="background:${s.color}"></i>${esc(s.nombre)}${s.datos.length ? "" : " (sin datos válidos)"}</span>`).join("") +
      `<span style="color:#ff4d5e"><i class="dash"></i>Alta ${fmt(l[`${p}_alta`])} ${u}</span>` +
      `<span style="color:#4dc3ff"><i class="dash"></i>Baja ${fmt(l[`${p}_baja`])} ${u}</span>`;
  });
}

function dibujar(cv, series, limites, unidad, cursor) {
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight;
  if (!W || !H) return;
  if (cv.width !== W * dpr || cv.height !== H * dpr) { cv.width = W * dpr; cv.height = H * dpr; }
  const c = cv.getContext("2d");
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.clearRect(0, 0, W, H);
  const M = { l: 46, r: 14, t: 10, b: 24 };
  const desde = HIST.desde, hasta = HIST.hasta, paso = HIST.paso_seg;

  let ymin = Infinity, ymax = -Infinity;
  for (const s of series) for (const d of s.datos) { ymin = Math.min(ymin, d[2]); ymax = Math.max(ymax, d[3]); }
  for (const l of limites) { ymin = Math.min(ymin, l.v); ymax = Math.max(ymax, l.v); }
  const margen = Math.max(1, (ymax - ymin) * 0.12);
  ymin = Math.floor(ymin - margen); ymax = Math.ceil(ymax + margen);

  const X = (t) => M.l + ((t - desde) / (hasta - desde)) * (W - M.l - M.r);
  const Y = (v) => M.t + (1 - (v - ymin) / (ymax - ymin)) * (H - M.t - M.b);
  cv._geo = { desde, hasta, M, W, series, unidad, paso };

  for (const l of limites) {
    c.fillStyle = l.zona === "arriba" ? "rgba(255,77,94,.07)" : "rgba(77,195,255,.07)";
    if (l.zona === "arriba") c.fillRect(M.l, M.t, W - M.l - M.r, Y(l.v) - M.t);
    else c.fillRect(M.l, Y(l.v), W - M.l - M.r, H - M.b - Y(l.v));
  }

  c.font = "11px Segoe UI, sans-serif";
  c.textBaseline = "middle"; c.textAlign = "right";
  const pasoY = [1, 2, 5, 10, 20].find((p) => (ymax - ymin) / p <= 7) || 20;
  for (let v = Math.ceil(ymin / pasoY) * pasoY; v <= ymax; v += pasoY) {
    c.strokeStyle = "rgba(139,154,181,.13)"; c.lineWidth = 1;
    c.beginPath(); c.moveTo(M.l, Math.round(Y(v)) + .5); c.lineTo(W - M.r, Math.round(Y(v)) + .5); c.stroke();
    c.fillStyle = "#8b9ab5"; c.fillText(`${v}${unidad === "%" ? "%" : "°"}`, M.l - 6, Y(v));
  }

  const span = hasta - desde;
  const pasoX = [600, 1800, 3600, 3 * 3600, 6 * 3600, 86400, 2 * 86400, 5 * 86400].find((p) => span / p <= 9) || 7 * 86400;
  const offset = new Date().getTimezoneOffset() * 60;
  c.textAlign = "center"; c.textBaseline = "top";
  for (let t = Math.ceil((desde - offset) / pasoX) * pasoX + offset; t <= hasta; t += pasoX) {
    const x = Math.round(X(t)) + .5;
    c.strokeStyle = "rgba(139,154,181,.10)";
    c.beginPath(); c.moveTo(x, M.t); c.lineTo(x, H - M.b); c.stroke();
    c.fillStyle = "#8b9ab5";
    c.fillText(pasoX >= 86400 ? dia(t) : (new Date(t * 1000).getHours() === 0 && span > 86400 ? dia(t) : hora(t)), x, H - M.b + 6);
  }

  c.setLineDash([6, 4]); c.lineWidth = 1.4;
  for (const l of limites) {
    c.strokeStyle = l.color;
    c.beginPath(); c.moveTo(M.l, Y(l.v)); c.lineTo(W - M.r, Y(l.v)); c.stroke();
  }
  c.setLineDash([]);

  const hueco = paso * 3;
  for (const s of series) {
    const tramos = [];
    let actual = [];
    s.datos.forEach((d, i) => {
      if (i && d[0] - s.datos[i - 1][0] > hueco) { tramos.push(actual); actual = []; }
      actual.push(d);
    });
    if (actual.length) tramos.push(actual);
    for (const tr of tramos) {
      if (paso > 60 && tr.length > 1) {
        c.fillStyle = s.color + "22";
        c.beginPath();
        tr.forEach((d, i) => (i ? c.lineTo(X(d[0]), Y(d[3])) : c.moveTo(X(d[0]), Y(d[3]))));
        for (let i = tr.length - 1; i >= 0; i--) c.lineTo(X(tr[i][0]), Y(tr[i][2]));
        c.closePath(); c.fill();
      }
      c.strokeStyle = s.color; c.lineWidth = 2; c.lineJoin = "round";
      c.beginPath();
      tr.forEach((d, i) => (i ? c.lineTo(X(d[0]), Y(d[1])) : c.moveTo(X(d[0]), Y(d[1]))));
      if (tr.length === 1) c.arc(X(tr[0][0]), Y(tr[0][1]), 2, 0, 7);
      c.stroke();
    }
  }

  const alta = limites[0].v, baja = limites[1].v;
  for (const s of series) for (const d of s.datos) {
    if (d[3] > alta || d[2] < baja) {
      c.fillStyle = d[3] > alta ? "#ff4d5e" : "#4dc3ff";
      c.fillRect(X(d[0]) - 1, H - M.b - 3, 2, 3);
    }
  }

  if (cursor !== undefined && cursor >= desde && cursor <= hasta) {
    c.strokeStyle = "rgba(230,236,245,.45)"; c.lineWidth = 1;
    c.beginPath(); c.moveTo(X(cursor), M.t); c.lineTo(X(cursor), H - M.b); c.stroke();
    for (const s of series) {
      const d = cercano(s.datos, cursor, paso);
      if (!d) continue;
      c.fillStyle = s.color; c.beginPath(); c.arc(X(d[0]), Y(d[1]), 3.5, 0, 7); c.fill();
    }
  }

  if (!series.some((s) => s.datos.length)) {
    c.fillStyle = "#8b9ab5"; c.textAlign = "center"; c.textBaseline = "middle"; c.font = "13px Segoe UI, sans-serif";
    c.fillText("Sin lecturas válidas en el período (el historial se acumula cada minuto)", W / 2, H / 2);
  }
}

function cercano(datos, t, paso) {
  let mejor = null, dist = Infinity;
  for (const d of datos) { const x = Math.abs(d[0] - t); if (x < dist) { dist = x; mejor = d; } }
  return dist <= paso * 1.5 ? mejor : null;
}

function activarTooltip(cv) {
  const tip = $("#tooltip");
  cv.addEventListener("mousemove", (ev) => {
    const g = cv._geo; if (!g) return;
    const r = cv.getBoundingClientRect();
    const x = ev.clientX - r.left;
    const t = g.desde + ((x - g.M.l) / (g.W - g.M.l - g.M.r)) * (g.hasta - g.desde);
    if (t < g.desde || t > g.hasta) { tip.classList.add("oculto"); dibujarTodo(); return; }
    dibujarTodo(t, cv);
    const u = g.unidad === "%" ? "%HR" : "°C";
    const filas = g.series.map((s) => {
      const d = cercano(s.datos, t, g.paso);
      if (!d) return "";
      const rango = g.paso > 60 ? ` <span style="color:#8b9ab5">(${fmt(d[2])}–${fmt(d[3])})</span>` : "";
      return `<div><i style="background:${s.color}"></i>${esc(s.nombre)}: <b style="display:inline;color:#e6ecf5">${fmt(d[1])} ${u}</b>${rango}</div>`;
    }).join("");
    const d = new Date(t * 1000);
    tip.innerHTML = `<b>${dia(t)}/${d.getFullYear()} ${hora(t)}</b>${filas || "<div>sin datos</div>"}`;
    tip.classList.remove("oculto");
    const cont = cv.closest(".graficos").getBoundingClientRect();
    let left = ev.clientX - cont.left + 14;
    if (left + tip.offsetWidth > cont.width - 8) left = ev.clientX - cont.left - tip.offsetWidth - 14;
    tip.style.left = left + "px";
    tip.style.top = (ev.clientY - cont.top + 12) + "px";
  });
  cv.addEventListener("mouseleave", () => { tip.classList.add("oculto"); dibujarTodo(); });
}

function pintarStats() {
  if (!EST || !HIST) return;
  const filas = EST.variables.map((v) => {
    const s = HIST.estadisticas[v.clave];
    return `<tr><td>${esc(v.sensor_nombre)} · ${v.magnitud === "temperatura" ? "temp." : "hum."}</td>
      <td>${s ? fmt(s.min) : "—"}</td><td>${s ? fmt(s.prom) : "—"}</td><td>${s ? fmt(s.max) : "—"}</td><td>${v.unidad}</td></tr>`;
  }).join("");
  $("#stats").innerHTML = `<thead><tr><th>Últimas ${rotuloRango()}</th><th style="text-align:right">Mín</th><th style="text-align:right">Prom</th><th style="text-align:right">Máx</th><th></th></tr></thead><tbody>${filas}</tbody>`;
}

// ---------------- eventos ----------------
async function cargarEventos() {
  try {
    const { eventos } = await api("/api/eventos?limite=300");
    const solo = $("#solo-alarmas").checked;
    const nombres = {};
    if (EST) {
      EST.variables.forEach((v) => { nombres[v.clave] = `${v.sensor_nombre} · ${v.magnitud}`; });
      EST.sensores.filter((s) => s.nodo).forEach((s) => { nombres[`equipo.${s.nodo}`] = s.nombre; });
    }
    const lista = eventos.filter((e) => !solo || EVENTOS_ALARMA.includes(e.tipo));
    notificarNuevos(eventos);
    $("#tabla-eventos tbody").innerHTML = lista.length ? lista.map((e) => `<tr>
      <td>${e.ts}</td><td class="t-${e.tipo}">${ETIQ[e.tipo] || (e.tipo === "normal" ? "NORMALIZADO" : e.tipo)}</td>
      <td>${esc(nombres[e.variable] || e.variable || "—")}</td>
      <td>${fmt(e.valor)}</td><td>${fmt(e.limite)}</td><td>${esc(e.detalle)}</td>
      <td>${e.reconocido === null ? "" : e.reconocido ? `✔ ${e.reconocido_ts.slice(11, 16)}` : "<b style='color:#ff4d5e'>pendiente</b>"}</td></tr>`).join("")
      : `<tr><td colspan="7" class="vacio">Sin eventos registrados</td></tr>`;
  } catch (e) { console.warn("eventos", e); }
}

let ultimoEventoId = null;
function notificarNuevos(eventos) {
  const max = eventos.length ? eventos[0].id : 0;
  if (ultimoEventoId !== null && "Notification" in window && Notification.permission === "granted") {
    eventos.filter((e) => e.id > ultimoEventoId && EVENTOS_ALARMA.includes(e.tipo)).forEach((e) =>
      new Notification("Clima Datacenter — " + (ETIQ[e.tipo] || e.tipo), { body: e.detalle + (e.valor !== null ? ` (${fmt(e.valor)})` : ""), tag: "clima-" + e.id }));
  }
  ultimoEventoId = max;
}

// ---------------- sonido ----------------
let audio = null, sonando = null;
function gestionarSonido() {
  const btn = $("#btn-sonido");
  const habilitado = EST && EST.ajustes.sonido && !silenciado;
  btn.classList.toggle("apagado", !habilitado);
  btn.textContent = silenciado ? "🔕 Silenciado" : audio && audio.state === "running" ? "🔔 Sonido" : "🔔 Activar sonido";
  const debe = habilitado && EST.sin_reconocer > 0;
  if (debe && !sonando) { sonando = setInterval(pitido, 1600); pitido(); }
  else if (!debe && sonando) { clearInterval(sonando); sonando = null; }
}
function pitido() {
  if (!audio || audio.state !== "running") return;
  const t = audio.currentTime;
  [0, 0.28].forEach((d) => {
    const o = audio.createOscillator(), g = audio.createGain();
    o.type = "square"; o.frequency.value = 880;
    g.gain.setValueAtTime(0.0001, t + d); g.gain.exponentialRampToValueAtTime(0.18, t + d + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t + d + 0.2);
    o.connect(g).connect(audio.destination); o.start(t + d); o.stop(t + d + 0.22);
  });
}
function desbloquearAudio() {
  // Los navegadores solo permiten audio tras una interaccion del usuario
  try {
    if (!audio) audio = new (window.AudioContext || window.webkitAudioContext)();
    if (audio.state === "suspended") audio.resume();
  } catch { /* sin WebAudio */ }
}
document.addEventListener("click", desbloquearAudio);

$("#btn-sonido").addEventListener("click", () => {
  if (audio && audio.state === "running") { silenciado = !silenciado; guardar("clima.silencio", silenciado); }
  else silenciado = false;
  if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
  setTimeout(gestionarSonido, 100);
});

$("#btn-reconocer").addEventListener("click", async () => {
  try { await api("/api/reconocer", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); }
  catch (e) { alert("No se pudo reconocer: " + e.message); }
  cargarEstado(); cargarEventos();
});

// ---------------- ajustes ----------------
const F = $("#form-ajustes");
const NOTA = "Las alarmas se confirman si la condición se mantiene durante el retardo. Fuera del rango válido la lectura se trata como falla de sensor. La discrepancia compara cada sonda con el retorno de los equipos de precisión.";
function llenarAjustes() {
  const aj = EST.ajustes;
  $("#limites-zonas").innerHTML = Object.entries(EST.zonas).map(([zona, nombre]) => {
    const l = lim(zona);
    const campo = (k, txt, paso) => `<label>${txt} <input type="number" step="${paso}" name="z.${zona}.${k}" value="${l[k]}" required></label>`;
    return `<fieldset><legend>${esc(nombre)}</legend>
      ${campo("temp_alta", "Temp. alta sobre (°C)", 0.1)}${campo("temp_baja", "Temp. baja bajo (°C)", 0.1)}
      ${campo("hum_alta", "Hum. alta sobre (%HR)", 0.5)}${campo("hum_baja", "Hum. baja bajo (%HR)", 0.5)}</fieldset>`;
  }).join("");
  for (const k of ["histeresis_temp", "histeresis_hum", "retardo_seg", "discrepancia_temp", "discrepancia_hum"]) F[k].value = aj[k];
  [F.rv_temp_lo.value, F.rv_temp_hi.value] = aj.rango_valido_temp;
  [F.rv_hum_lo.value, F.rv_hum_hi.value] = aj.rango_valido_hum;
  $("#activos").innerHTML = EST.sensores.map((s) =>
    `<label><input type="checkbox" data-sensor="${s.id}" ${s.activo ? "checked" : ""}> ${esc(s.nombre)}</label>`).join("") +
    `<label><input type="checkbox" name="sonido" ${aj.sonido ? "checked" : ""}> Sonido de alarma</label>`;
  $$("input", F).forEach((i) => i.classList.remove("cambiado"));
  if (!$("#msg-ajustes").className.includes("ok") && !$("#msg-ajustes").className.includes("error")) $("#msg-ajustes").textContent = NOTA;
}
F.addEventListener("input", (ev) => { formEditado = true; ev.target.classList.add("cambiado"); });
$("#btn-deshacer").addEventListener("click", () => { formEditado = false; mensaje("", ""); llenarAjustes(); });

function mensaje(txt, clase) {
  const m = $("#msg-ajustes");
  m.textContent = txt || NOTA;
  m.className = "nota " + (clase || "");
}

F.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const n = (k) => Number(F[k].value);
  const zonas = {};
  $$("[name^='z.']", F).forEach((i) => {
    const [, zona, k] = i.name.split(".");
    (zonas[zona] = zonas[zona] || {})[k] = Number(i.value);
  });
  const cuerpo = {
    zonas,
    histeresis_temp: n("histeresis_temp"), histeresis_hum: n("histeresis_hum"),
    retardo_seg: n("retardo_seg"),
    discrepancia_temp: n("discrepancia_temp"), discrepancia_hum: n("discrepancia_hum"),
    rango_valido_temp: [n("rv_temp_lo"), n("rv_temp_hi")],
    rango_valido_hum: [n("rv_hum_lo"), n("rv_hum_hi")],
    sonido: F.sonido.checked,
    sensores_activos: Object.fromEntries($$("[data-sensor]", F).map((i) => [i.dataset.sensor, i.checked])),
    clave: F.clave.value,
  };
  try {
    await api("/api/ajustes", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo) });
    formEditado = false; F.clave.value = "";
    mensaje("✔ Setpoints guardados", "ok");
    await cargarEstado(); cargarHistorial(); cargarEventos();
  } catch (e) {
    if (/clave/i.test(e.message)) $("#clave-ajustes").classList.remove("oculto");
    mensaje("✖ " + e.message, "error");
  }
});

// ---------------- excel ----------------
const FX = $("#form-excel");
function fijarFechas(desde, hasta) { FX.desde.value = fechaInput(desde); FX.hasta.value = fechaInput(hasta); }
fijarFechas(new Date(Date.now() - 6 * 86400e3), new Date());
$$(".atajos button").forEach((b) => b.addEventListener("click", () => {
  const hoy = new Date();
  if (b.dataset.mes) {
    fijarFechas(new Date(hoy.getFullYear(), hoy.getMonth() - 1, 1), new Date(hoy.getFullYear(), hoy.getMonth(), 0));
  } else {
    const d = Number(b.dataset.d);
    fijarFechas(new Date(Date.now() - Math.max(0, d - 1) * 86400e3), hoy);
  }
}));
FX.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const q = new URLSearchParams({ desde: FX.desde.value, hasta: FX.hasta.value, paso: FX.paso.value });
  window.location.href = "/api/excel?" + q;
});

// ---------------- arranque ----------------
$$("#rango button").forEach((b) => {
  b.classList.toggle("activo", Number(b.dataset.h) === horas);
  b.addEventListener("click", () => {
    horas = Number(b.dataset.h); guardar("clima.horas", horas);
    $$("#rango button").forEach((x) => x.classList.toggle("activo", x === b));
    cargarHistorial();
  });
});
$("#solo-alarmas").addEventListener("change", cargarEventos);
window.addEventListener("resize", () => dibujarTodo());

(async () => {
  await cargarEstado();
  await cargarHistorial();
  await cargarEventos();
  setInterval(cargarEstado, 5000);
  setInterval(cargarHistorial, 60000);
  setInterval(cargarEventos, 30000);
})();
