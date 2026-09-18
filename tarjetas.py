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
def desde_fragmentos(encontrados, titulo="Resultados"):
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

    return [
        {
            "version": VERSION,
            "createSurface": {"surfaceId": SUPERFICIE, "catalogId": CATALOGO},
        },
        {
            "version": VERSION,
            "updateComponents": {
                "surfaceId": SUPERFICIE,
                "components": componentes,
            },
        },
    ]


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
def panel(meses, fragmentos, titulo="Perfil en cifras"):
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
         "x": {"field": "lenguaje"},
         "series": [{"field": "repos"}],
         "height": 240},
    ]

    return [
        {"version": VERSION,
         "createSurface": {"surfaceId": SUPERFICIE,
                           "catalogId": CATALOGO_CHARTS}},
        {"version": VERSION,
         "updateDataModel": {"surfaceId": SUPERFICIE, "path": "/",
                             "value": datos}},
        {"version": VERSION,
         "updateComponents": {"surfaceId": SUPERFICIE,
                              "components": componentes}},
    ]
