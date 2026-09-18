# Almacen de embeddings: guarda los vectores y busca entre ellos.
#
# Con COSMOS_URL configurada los vectores viven en Azure Cosmos DB y la
# comparacion la hace el servicio con VectorDistance. Sin ella, se usa
# el indice.json local.
#
# Ademas del indice que construye indexar.py, aqui se puede agregar
# informacion nueva en caliente, cuando una pregunta necesita algo que
# todavia no estaba indexado.

import hashlib
import os

import numpy as np
from dotenv import load_dotenv

import llm

load_dotenv()

BASE = "cv-agent"
CONTENEDOR = "fragmentos"
DIMENSIONES = 768

# El coseno es el que corresponde porque los vectores van normalizados.
POLITICA_VECTORES = {
    "vectorEmbeddings": [
        {
            "path": "/vector",
            "dataType": "float32",
            "distanceFunction": "cosine",
            "dimensions": DIMENSIONES,
        }
    ]
}

# Los vectores no se indexan como campo normal: se excluyen del indice
# comun y se declara un indice vectorial aparte.
POLITICA_INDICE = {
    "indexingMode": "consistent",
    "includedPaths": [{"path": "/*"}],
    "excludedPaths": [{"path": "/vector/*"}],
    "vectorIndexes": [{"path": "/vector", "type": "diskANN"}],
}


def configurado():
    return bool(os.environ.get("COSMOS_URL") and os.environ.get("COSMOS_KEY"))


def _contenedor(crear=False):
    from azure.cosmos import CosmosClient, PartitionKey

    cliente = CosmosClient(
        os.environ["COSMOS_URL"], credential=os.environ["COSMOS_KEY"]
    )

    if not crear:
        return cliente.get_database_client(BASE).get_container_client(CONTENEDOR)

    base = cliente.create_database_if_not_exists(id=BASE)
    return base.create_container_if_not_exists(
        id=CONTENEDOR,
        partition_key=PartitionKey(path="/fuente"),
        indexing_policy=POLITICA_INDICE,
        vector_embedding_policy=POLITICA_VECTORES,
    )


# El id sale del contenido, no del titulo: un PDF se parte en varios
# fragmentos que comparten titulo y se pisarian entre si. Con el hash,
# reindexar lo mismo actualiza el documento y no lo duplica.
def identificador(fragmento):
    firma = (fragmento["fuente"] + fragmento["texto"]).encode("utf-8")
    return hashlib.sha1(firma).hexdigest()


def normalizar(vectores):
    matriz = np.array(vectores, dtype="float32")
    matriz /= np.linalg.norm(matriz, axis=1, keepdims=True)
    return matriz


# Reemplaza el indice completo. Lo llama indexar.py.
def subir(fragmentos, vectores):
    contenedor = _contenedor(crear=True)
    for fragmento, vector in zip(fragmentos, vectores):
        contenedor.upsert_item(
            {
                "id": identificador(fragmento),
                "fuente": fragmento["fuente"],
                "titulo": fragmento["titulo"],
                "texto": fragmento["texto"],
                "vector": vector,
            }
        )
    return len(fragmentos)


# La busqueda la hace Cosmos: VectorDistance calcula la similitud y el
# ORDER BY trae solo los mas parecidos.
def buscar_en_cosmos(consulta, fuente=None, cuantos=3):
    contenedor = _contenedor()

    filtro = ""
    parametros = [
        {"name": "@vector", "value": consulta},
        {"name": "@cuantos", "value": cuantos},
    ]
    if fuente:
        filtro = "WHERE c.fuente = @fuente"
        parametros.append({"name": "@fuente", "value": fuente})

    sql = f"""
        SELECT TOP @cuantos c.fuente, c.titulo, c.texto,
               VectorDistance(c.vector, @vector) AS similitud
        FROM c {filtro}
        ORDER BY VectorDistance(c.vector, @vector)
    """

    return list(
        contenedor.query_items(
            query=sql, parameters=parametros, enable_cross_partition_query=True
        )
    )


# ------------------------------------------------- indexado en caliente

# Que titulos hay ya guardados, para no volver a indexar lo mismo.
def titulos(fuente):
    if configurado():
        contenedor = _contenedor()
        filas = contenedor.query_items(
            query="SELECT c.titulo FROM c WHERE c.fuente = @fuente",
            parameters=[{"name": "@fuente", "value": fuente}],
            enable_cross_partition_query=True,
        )
        return {f["titulo"] for f in filas}

    return {f["titulo"] for f in _memoria["fragmentos"] if f["fuente"] == fuente}


# Vectoriza fragmentos nuevos y los deja disponibles para buscar, sin
# tener que reconstruir el indice entero.
def agregar(nuevos):
    if not nuevos:
        return 0

    vectores = normalizar(llm.embeber([f["texto"] for f in nuevos])).tolist()

    if configurado():
        contenedor = _contenedor(crear=True)
        for fragmento, vector in zip(nuevos, vectores):
            contenedor.upsert_item(
                {
                    "id": identificador(fragmento),
                    "fuente": fragmento["fuente"],
                    "titulo": fragmento["titulo"],
                    "texto": fragmento["texto"],
                    "vector": vector,
                }
            )
        return len(nuevos)

    # Sin Cosmos, el indice vive en la memoria del proceso.
    _memoria["fragmentos"].extend(nuevos)
    _memoria["vectores"].extend(vectores)
    return len(nuevos)


# ------------------------------------------------- indice en el arranque

# Sin Cosmos el indice vive aqui y se reconstruye en cada arranque.
_memoria = {"fragmentos": [], "vectores": []}

_listo = False


def cuantos_hay():
    if configurado():
        contenedor = _contenedor(crear=True)
        filas = list(
            contenedor.query_items(
                query="SELECT VALUE COUNT(1) FROM c",
                enable_cross_partition_query=True,
            )
        )
        return filas[0] if filas else 0
    return len(_memoria["fragmentos"])


# Se llama antes de la primera busqueda. Si no hay nada guardado, lee las
# fuentes y construye el indice; despues no vuelve a hacer nada.
def asegurar_indice():
    global _listo
    if _listo:
        return

    if cuantos_hay() == 0:
        import indexar

        print("Indice vacio: construyendolo desde las fuentes...")
        fragmentos = indexar.desde_documentos() + indexar.desde_github()
        agregar(fragmentos)
        print(f"  {len(fragmentos)} fragmentos indexados")

    _listo = True


def en_memoria():
    return _memoria


# El texto de todos los fragmentos de una fuente. Lo usa la herramienta
# que calcula la experiencia, que necesita leer las fechas completas.
def textos_de(fuente):
    asegurar_indice()

    if configurado():
        contenedor = _contenedor()
        filas = contenedor.query_items(
            query="SELECT c.texto FROM c WHERE c.fuente = @fuente",
            parameters=[{"name": "@fuente", "value": fuente}],
            enable_cross_partition_query=True,
        )
        return [f["texto"] for f in filas]

    return [
        f["texto"] for f in _memoria["fragmentos"] if f["fuente"] == fuente
    ]


# Todos los fragmentos guardados, para las vistas que necesitan el
# conjunto completo en vez de una busqueda.
def todos():
    asegurar_indice()

    if configurado():
        contenedor = _contenedor()
        filas = list(
            contenedor.query_items(
                query="SELECT c.fuente, c.titulo, c.texto FROM c",
                enable_cross_partition_query=True,
            )
        )
        return filas, len(filas)

    return _memoria["fragmentos"], len(_memoria["fragmentos"])
