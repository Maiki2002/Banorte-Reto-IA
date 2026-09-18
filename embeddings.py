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
                query="SELECT VALUE COUNT(1) FROM c WHERE c.fuente != 'control'",
                enable_cross_partition_query=True,
            )
        )
        return filas[0] if filas else 0
    return len(_memoria["fragmentos"])


# Una firma del contenido de cv/. Si cambia, es que se agregaron o
# editaron documentos y hay que volver a indexarlos.
def firma_documentos():
    import pathlib

    resumen = hashlib.sha1()
    carpeta = pathlib.Path("cv")
    for archivo in sorted(carpeta.glob("*")):
        if archivo.suffix.lower() not in (".pdf", ".md", ".txt"):
            continue
        if archivo.name == "LEEME.md":
            continue
        resumen.update(archivo.name.encode())
        resumen.update(archivo.read_bytes())
    return resumen.hexdigest()


# La firma se guarda como un documento mas, en su propia particion.
def _firma_guardada():
    if not configurado():
        return _memoria.get("firma")
    try:
        doc = _contenedor().read_item(item="firma", partition_key="control")
        return doc.get("valor")
    except Exception:
        return None


def _guardar_firma(valor):
    if not configurado():
        _memoria["firma"] = valor
        return
    _contenedor(crear=True).upsert_item(
        {"id": "firma", "fuente": "control", "valor": valor}
    )


def _borrar_documentos():
    if not configurado():
        indice = _memoria
        pares = [
            (f, v)
            for f, v in zip(indice["fragmentos"], indice["vectores"])
            if f["fuente"] != "documentos"
        ]
        indice["fragmentos"] = [f for f, _ in pares]
        indice["vectores"] = [v for _, v in pares]
        return

    contenedor = _contenedor()
    for fila in contenedor.query_items(
        query="SELECT c.id FROM c WHERE c.fuente = 'documentos'",
        enable_cross_partition_query=True,
    ):
        contenedor.delete_item(item=fila["id"], partition_key="documentos")


# Se llama antes de la primera busqueda. Construye el indice si esta
# vacio, y reindexa los documentos si cambiaron desde la ultima vez.
def asegurar_indice():
    global _listo
    if _listo:
        return
    _listo = True

    import indexar

    vacio = cuantos_hay() == 0
    if vacio:
        print("Indice vacio: construyendolo desde las fuentes...")
        fragmentos = indexar.desde_documentos() + indexar.desde_github()
        agregar(fragmentos)
        _guardar_firma(firma_documentos())
        print(f"  {len(fragmentos)} fragmentos indexados")
        return

    # El indice ya existe: solo hay que rehacer los documentos si el
    # contenido de cv/ cambio.
    actual = firma_documentos()
    if actual != _firma_guardada():
        print("Los documentos cambiaron: reindexandolos...")
        _borrar_documentos()
        nuevos = indexar.desde_documentos()
        agregar(nuevos)
        _guardar_firma(actual)
        print(f"  {len(nuevos)} fragmentos de documentos actualizados")


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
                query="SELECT c.fuente, c.titulo, c.texto FROM c WHERE c.fuente != 'control'",
                enable_cross_partition_query=True,
            )
        )
        return filas, len(filas)

    return _memoria["fragmentos"], len(_memoria["fragmentos"])
