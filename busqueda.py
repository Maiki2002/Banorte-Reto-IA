import json
import pathlib

import numpy as np

import llm

ARCHIVO = pathlib.Path("indice.json")

UMBRAL = 0.55

_fragmentos = None
_vectores = None

def cargar():
    
    global _fragmentos, _vectores
    if _fragmentos is None:
        if not ARCHIVO.exists():
            raise RuntimeError(f"Falta {ARCHIVO}. Corre: python indexar.py")
        datos = json.loads(ARCHIVO.read_text(encoding="utf-8"))
        _fragmentos = datos["fragmentos"]
        _vectores = np.array(datos["vectores"], dtype="float32")
    return _fragmentos, _vectores

def buscar(pregunta, fuente=None, cuantos=3):
    fragmentos, vectores = cargar()

    consulta = np.array(
        llm.embeber([pregunta], tipo="RETRIEVAL_QUERY")[0], dtype="float32"
    )
    consulta /= np.linalg.norm(consulta)

    similitudes = vectores @ consulta

    if fuente:
        for i, fragmento in enumerate(fragmentos):
            if fragmento["fuente"] != fuente:
                similitudes[i] = -1

    encontrados = []
    for i in np.argsort(similitudes)[::-1][:cuantos]:
        if similitudes[i] < UMBRAL:
            continue
        encontrados.append(
            {
                "fuente": fragmentos[i]["fuente"],
                "titulo": fragmentos[i]["titulo"],
                "texto": fragmentos[i]["texto"],
                "similitud": round(float(similitudes[i]), 3),
            }
        )

    return encontrados
