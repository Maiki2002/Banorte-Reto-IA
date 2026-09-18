# Lee las fuentes del agente: los PDF de cv/ y los repos de GitHub.
#
# No se corre a mano: embeddings.asegurar_indice() lo usa cuando el
# almacen esta vacio, y agente.py cuando aparecen repos nuevos.

import pathlib

import httpx
from pypdf import PdfReader

import httpx
from pypdf import PdfReader

CARPETA = pathlib.Path("cv")
USUARIO_GITHUB = "Maiki2002"

MINIMO = 60
MAXIMO = 700


def limpiar(texto):
    lineas = [" ".join(l.split()) for l in texto.splitlines()]
    return "\n".join(l for l in lineas if l)

def partir(texto):
    bloques = []
    actual = []
    largo = 0

    for linea in texto.split("\n"):
        if largo + len(linea) > MAXIMO and actual:
            bloques.append("\n".join(actual))
            actual, largo = [], 0
        actual.append(linea)
        largo += len(linea)

    if actual:
        bloques.append("\n".join(actual))

    return [b for b in bloques if len(b) >= MINIMO]


def desde_documentos():
    fragmentos = []

    for pdf in sorted(CARPETA.glob("*.pdf")):
        texto = ""
        for pagina in PdfReader(pdf).pages:
            texto += (pagina.extract_text() or "") + "\n"
        for bloque in partir(limpiar(texto)):
            fragmentos.append(
                {"fuente": "documentos", "titulo": pdf.stem, "texto": bloque}
            )

    for md in sorted(CARPETA.glob("*.md")):
        if md.name == "LEEME.md":
            continue
        for bloque in partir(limpiar(md.read_text(encoding="utf-8"))):
            fragmentos.append(
                {"fuente": "documentos", "titulo": md.stem, "texto": bloque}
            )

    return fragmentos

def desde_github():
    fragmentos = []
    base = "https://api.github.com"

    with httpx.Client(timeout=20) as web:
        respuesta = web.get(
            f"{base}/users/{USUARIO_GITHUB}/repos",
            params={"per_page": 100, "sort": "updated"},
        )

        # Sin token la API permite 60 peticiones por hora. Cuando se
        # agotan devuelve un error en vez de la lista, y conviene seguir
        # con los documentos en vez de tirar todo el indexado.
        repos = respuesta.json()
        if respuesta.status_code != 200 or not isinstance(repos, list):
            print(f"  GitHub no respondio ({respuesta.status_code}), se omite")
            return []

        for repo in repos:
            if repo.get("fork"):
                continue

            ficha = [
                f"Repositorio: {repo['name']}",
                f"Lenguaje principal: {repo.get('language') or 'no especificado'}",
                f"Ultima actualizacion: {repo['updated_at'][:10]}",
                f"URL: {repo['html_url']}",
            ]
            if repo.get("description"):
                ficha.append(f"Descripcion: {repo['description']}")

            respuesta = web.get(
                f"{base}/repos/{USUARIO_GITHUB}/{repo['name']}/readme",
                headers={"Accept": "application/vnd.github.raw"},
            )
            if respuesta.status_code == 200 and len(respuesta.text) > MINIMO:
                ficha.append("README:")
                ficha.append(limpiar(respuesta.text)[:2000])

            fragmentos.append(
                {"fuente": "github", "titulo": repo["name"], "texto": "\n".join(ficha)}
            )

    return fragmentos


