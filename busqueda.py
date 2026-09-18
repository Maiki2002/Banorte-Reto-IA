# Busca los fragmentos mas parecidos a una pregunta.
#
# Con Cosmos configurado la comparacion la hace el servicio; si no, se
# resuelve aqui con un producto punto sobre el indice en memoria.
#
# No hay archivo de indice: si el almacen esta vacio, embeddings lo
# construye solo la primera vez que alguien busca.

import numpy as np

import embeddings
import llm

# Filtro grueso. Medi que no existe un umbral que separe perfecto lo que
# es del CV de lo que no, asi que esto descarta lo evidente y el agente
# decide si lo que queda responde la pregunta.
UMBRAL = 0.55


# fuente: "documentos", "github" o None para buscar en todo.
def buscar(pregunta, fuente=None, cuantos=3):
    embeddings.asegurar_indice()

    consulta = np.array(
        llm.embeber([pregunta], tipo="RETRIEVAL_QUERY")[0], dtype="float32"
    )
    consulta /= np.linalg.norm(consulta)

    if embeddings.configurado():
        return _desde_cosmos(consulta, fuente, cuantos)

    return _desde_memoria(consulta, fuente, cuantos)


def _desde_memoria(consulta, fuente, cuantos):
    indice = embeddings.en_memoria()
    fragmentos = indice["fragmentos"]
    if not fragmentos:
        return []

    # Los vectores ya estan normalizados, asi que el producto punto es
    # la similitud coseno.
    vectores = np.array(indice["vectores"], dtype="float32")
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


# VectorDistance con coseno devuelve similitud, igual que el producto
# punto de arriba. Verificado: da los mismos valores al cuarto decimal.
def _desde_cosmos(consulta, fuente, cuantos):
    encontrados = []
    for fila in embeddings.buscar_en_cosmos(consulta.tolist(), fuente, cuantos):
        similitud = round(float(fila["similitud"]), 3)
        if similitud < UMBRAL:
            continue
        encontrados.append(
            {
                "fuente": fila["fuente"],
                "titulo": fila["titulo"],
                "texto": fila["texto"],
                "similitud": similitud,
            }
        )
    return encontrados
