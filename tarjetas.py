# Construye tarjetas en formato A2UI a partir de lo que recupera el RAG.
#
# Es un experimento: la plataforma habla Open Responses, pero A2UI
# necesita ademas un renderizador en el cliente. Por eso va detras de la
# variable A2UI: si no esta puesta, el agente responde solo texto y nada
# de esto se ejecuta.
#
# Formato: A2UI v0.9  ·  https://a2ui.org/reference/messages/

import json
import os

VERSION = "v0.9"
CATALOGO = "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"
SUPERFICIE = "perfil"


def activado():
    return os.environ.get("A2UI", "").lower() in ("1", "true", "si")


# Una tarjeta por fragmento recuperado: titulo, fuente y un extracto.
def desde_fragmentos(encontrados, titulo="Resultados", preguntas=None):
    componentes = [
        {"id": "root", "component": "Column", "children": ["titulo"] +
         [f"card{i}" for i in range(len(encontrados))]},
        {"id": "titulo", "component": "Text", "text": titulo},
    ]

    for i, fragmento in enumerate(encontrados):
        extracto = " ".join(fragmento["texto"].split())[:180]
        componentes.extend(
            [
                {"id": f"card{i}", "component": "Card", "child": f"col{i}"},
                {
                    "id": f"col{i}",
                    "component": "Column",
                    "children": [f"tit{i}", f"txt{i}"],
                },
                {"id": f"tit{i}", "component": "Text",
                 "text": fragmento["titulo"]},
                {"id": f"txt{i}", "component": "Text", "text": extracto},
            ]
        )

    hijos = componentes[0]["children"]
    extra_ids, extra = seguimiento(preguntas or [], "tc")
    componentes[0]["children"] = hijos + extra_ids
    return envoltura(componentes[1:] + extra, componentes[0]["children"])


# La plataforma lee las tarjetas de un item de output de tipo
# function_call con este nombre, no del texto de la respuesta.
HERRAMIENTA = "ajac-zero:a2ui"


# La plataforma muestra la llamada como "Pendiente" hasta que recibe su
# resultado, asi que van los dos items: la llamada y su salida.
def como_items(mensajes, ident, call_id):
    llamada = {
        "type": "function_call",
        "id": ident,
        "call_id": call_id,
        "name": HERRAMIENTA,
        "arguments": json.dumps({"messages": mensajes}, ensure_ascii=False),
        "status": "completed",
    }
    # El output va como el array de mensajes tal cual: la plataforma lo
    # valida comprobando que cada elemento tenga createSurface,
    # updateComponents, updateDataModel o deleteSurface.
    salida = {
        "type": "function_call_output",
        "id": ident.replace("fc_", "fco_"),
        "call_id": call_id,
        "output": json.dumps(mensajes, ensure_ascii=False),
        "status": "completed",
    }
    return [llamada, salida]


# ------------------------------------------------------------- panel

CATALOGO_CHARTS = (
    "https://github.com/ajac-zero/a2ui-catalogs/blob/main/catalogs/charts/v1/catalog.json"
)


# Saca el lenguaje de cada repositorio de los fragmentos indexados, que
# ya traen la ficha que devolvio la API de GitHub.
def lenguajes_de(fragmentos):
    cuenta = {}
    for f in fragmentos:
        if f.get("fuente") != "github":
            continue
        for linea in f["texto"].splitlines():
            if linea.startswith("Lenguaje principal:"):
                nombre = linea.split(":", 1)[1].strip()
                if nombre and nombre != "no especificado":
                    cuenta[nombre] = cuenta.get(nombre, 0) + 1
                break
    return sorted(cuenta.items(), key=lambda x: -x[1])


# Un panel con las cifras del perfil y la distribucion de lenguajes.
# Las cifras salen de datos reales: los periodos del CV y los repos
# indexados, no de lo que estime el modelo.
def panel(meses, fragmentos, titulo="Perfil en cifras", preguntas=None):
    lenguajes = lenguajes_de(fragmentos)
    repos = sum(1 for f in fragmentos if f.get("fuente") == "github")

    datos = {
        "lenguajes": [
            {"lenguaje": nombre, "repos": n} for nombre, n in lenguajes[:6]
        ]
    }

    componentes = [
        {"id": "root", "component": "Column",
         "children": ["titulo", "cifras", "sep", "grafica"]},
        {"id": "titulo", "component": "Text", "text": titulo},
        {"id": "cifras", "component": "Row",
         "children": ["s1", "s2", "s3"]},
        {"id": "s1", "component": "Stat",
         "label": "Experiencia", "value": meses,
         "description": "meses calculados de su CV"},
        {"id": "s2", "component": "Stat",
         "label": "Repositorios", "value": repos,
         "description": "publicos en GitHub"},
        {"id": "s3", "component": "Stat",
         "label": "Lenguajes", "value": len(lenguajes),
         "description": "distintos en sus repos"},
        {"id": "sep", "component": "Divider"},
        {"id": "grafica", "component": "Chart",
         "title": "Repositorios por lenguaje",
         "variant": "bar",
         "data": {"path": "/lenguajes"},
         "x": {"key": "lenguaje", "label": "Lenguaje"},
         "series": [{"key": "repos", "label": "Repositorios"}],
         "height": 240},
    ]

    hijos = componentes[0]["children"]
    extra_ids, extra = seguimiento(preguntas or [], "pn")
    return envoltura(componentes[1:] + extra, hijos + extra_ids,
                     catalogo=CATALOGO_CHARTS, datos=datos)


# --------------------------------------------------- botones y vistas

# Un boton que dispara un evento hacia el agente. La plataforma reenvia
# la interaccion, asi que quien pregunta puede seguir la conversacion
# con un clic en vez de escribir.
def boton(ident, texto, pregunta):
    return [
        {"id": ident, "component": "Button", "child": ident + "_t",
         "variant": "default",
         "action": {"event": {"name": "preguntar",
                              "context": {"pregunta": pregunta}}}},
        {"id": ident + "_t", "component": "Text", "text": texto},
    ]


# Fila de botones de seguimiento. Va al pie de cualquier vista.
def seguimiento(preguntas, prefijo="sig"):
    if not preguntas:
        return [], []
    ids = []
    componentes = []
    for i, texto in enumerate(preguntas[:3]):
        ident = f"{prefijo}{i}"
        ids.append(ident)
        componentes.extend(boton(ident, texto, texto))
    fila = prefijo + "_fila"
    return [fila], [
        {"id": fila, "component": "Row", "children": ids}
    ] + componentes


def envoltura(componentes, raiz_hijos, catalogo=None, datos=None):
    mensajes = [
        {"version": VERSION,
         "createSurface": {"surfaceId": SUPERFICIE,
                           "catalogId": catalogo or CATALOGO}},
    ]
    if datos is not None:
        mensajes.append(
            {"version": VERSION,
             "updateDataModel": {"surfaceId": SUPERFICIE, "path": "/",
                                 "value": datos}}
        )
    mensajes.append(
        {"version": VERSION,
         "updateComponents": {
             "surfaceId": SUPERFICIE,
             "components": [{"id": "root", "component": "Column",
                             "children": raiz_hijos}] + componentes}}
    )
    return mensajes


# Los empleos y proyectos con fecha, como una linea de tiempo.
def linea_de_tiempo(periodos, titulo="Trayectoria", preguntas=None):
    hijos = ["titulo"]
    comps = [{"id": "titulo", "component": "Text", "text": titulo}]

    for i, (desde, hasta, que) in enumerate(periodos[:6]):
        hijos.append(f"h{i}")
        comps.extend([
            {"id": f"h{i}", "component": "Card", "child": f"hc{i}"},
            {"id": f"hc{i}", "component": "Row",
             "children": [f"hi{i}", f"hf{i}", f"ht{i}"]},
            {"id": f"hi{i}", "component": "Icon", "name": "calendar"},
            {"id": f"hf{i}", "component": "Text",
             "text": f"{desde} — {hasta}"},
            {"id": f"ht{i}", "component": "Text", "text": que},
        ])

    extra_ids, extra = seguimiento(preguntas or [], "tl")
    return envoltura(comps + extra, hijos + extra_ids)


# El stack agrupado por area, con una grafica de cuantas tecnologias
# tiene en cada una.
def stack(areas, titulo="Stack tecnico", preguntas=None):
    datos = {"areas": [{"area": a, "cuantas": len(t)} for a, t in areas]}

    hijos = ["titulo", "grafica"]
    comps = [
        {"id": "titulo", "component": "Text", "text": titulo},
        {"id": "grafica", "component": "Chart",
         "title": "Tecnologias por area", "variant": "bar",
         "data": {"path": "/areas"},
         "x": {"key": "area", "label": "Area"},
         "series": [{"key": "cuantas", "label": "Tecnologias"}],
         "height": 220},
    ]

    for i, (area, tecnologias) in enumerate(areas[:6]):
        hijos.append(f"a{i}")
        comps.extend([
            {"id": f"a{i}", "component": "Card", "child": f"ac{i}"},
            {"id": f"ac{i}", "component": "Column",
             "children": [f"an{i}", f"at{i}"]},
            {"id": f"an{i}", "component": "Text", "text": area},
            {"id": f"at{i}", "component": "Text",
             "text": ", ".join(tecnologias)},
        ])

    extra_ids, extra = seguimiento(preguntas or [], "st")
    return envoltura(comps + extra, hijos + extra_ids,
                     catalogo=CATALOGO_CHARTS, datos=datos)


# Iconos que trae la plataforma (son de Lucide, los vi en su bundle).
ICONOS = {
    "persona": "circle-user-round",
    "codigo": "file-code",
    "cerebro": "brain",
    "calendario": "calendar-days",
    "reloj": "clock",
    "carpeta": "folder",
    "estrella": "star",
    "herramienta": "wrench",
    "panel": "layout-dashboard",
    "correo": "mail",
    "lugar": "map-pin",
    "enviar": "send",
}


# La carta de presentacion: quien es, sus cifras y por donde empezar.
# Se muestra cuando alguien saluda o pregunta quien es el candidato.
def presentacion(nombre, titular, meses, repos, lenguajes, preguntas=None):
    datos = {"cifras": [
        {"que": "Meses", "valor": meses},
        {"que": "Repos", "valor": repos},
        {"que": "Lenguajes", "valor": lenguajes},
    ]}

    hijos = ["cab", "sep1", "cifras", "sep2", "guia"]
    comps = [
        {"id": "cab", "component": "Card", "child": "cabcol"},
        {"id": "cabcol", "component": "Column",
         "children": ["cabrow", "titular"]},
        {"id": "cabrow", "component": "Row", "children": ["ico", "nombre"]},
        {"id": "ico", "component": "Icon", "name": ICONOS["persona"]},
        {"id": "nombre", "component": "Text", "text": nombre},
        {"id": "titular", "component": "Text", "text": titular},

        {"id": "sep1", "component": "Divider"},

        {"id": "cifras", "component": "Row", "children": ["c1", "c2", "c3"]},
        {"id": "c1", "component": "Stat", "label": "⏳ Experiencia",
         "value": meses, "description": "meses calculados de su CV"},
        {"id": "c2", "component": "Stat", "label": "💻 Repositorios",
         "value": repos, "description": "públicos en GitHub"},
        {"id": "c3", "component": "Stat", "label": "🎯 Lenguajes",
         "value": lenguajes, "description": "distintos en sus repos"},

        {"id": "sep2", "component": "Divider"},
        {"id": "guia", "component": "Text",
         "text": "¿Por dónde quieres empezar?"},
    ]

    extra_ids, extra = seguimiento(preguntas or [], "pr")
    return envoltura(comps + extra, hijos + extra_ids,
                     catalogo=CATALOGO_CHARTS, datos=datos)


# Las habilidades como barras de nivel, una por area.
def habilidades(areas, titulo="🧠 Habilidades por área", preguntas=None):
    tope = max((len(t) for _, t in areas), default=1)

    hijos = ["titulo"]
    comps = [{"id": "titulo", "component": "Text", "text": titulo}]

    for i, (area, tecnologias) in enumerate(areas[:6]):
        hijos.append(f"h{i}")
        comps.extend([
            {"id": f"h{i}", "component": "Card", "child": f"hc{i}"},
            {"id": f"hc{i}", "component": "Column",
             "children": [f"hr{i}", f"hp{i}", f"ht{i}"]},
            {"id": f"hr{i}", "component": "Row",
             "children": [f"hi{i}", f"hn{i}"]},
            {"id": f"hi{i}", "component": "Icon",
             "name": ICONOS["herramienta"]},
            {"id": f"hn{i}", "component": "Text", "text": area},
            {"id": f"hp{i}", "component": "Progress",
             "label": f"{len(tecnologias)} tecnologías",
             "value": len(tecnologias), "max": tope},
            {"id": f"ht{i}", "component": "Text",
             "text": ", ".join(tecnologias)},
        ])

    extra_ids, extra = seguimiento(preguntas or [], "hb")
    return envoltura(comps + extra, hijos + extra_ids,
                     catalogo=CATALOGO_CHARTS)
