import json
import pathlib

import httpx
import numpy as np
from pypdf import PdfReader

import llm

CARPETA = pathlib.Path("cv")
SALIDA = pathlib.Path("indice.json")
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
        repos = web.get(
            f"{base}/users/{USUARIO_GITHUB}/repos",
            params={"per_page": 100, "sort": "updated"},
        ).json()

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


def main():
    print("Recolectando fuentes...")
    documentos = desde_documentos()
    github = desde_github()
    fragmentos = documentos + github
    print(f"  documentos: {len(documentos)}")
    print(f"  github:     {len(github)}")

    print(f"\nCalculando embeddings de {len(fragmentos)} fragmentos...")
    vectores = llm.embeber([f["texto"] for f in fragmentos])

    # Los normalizo aqui para que buscar sea un producto punto.
    matriz = np.array(vectores, dtype="float32")
    matriz /= np.linalg.norm(matriz, axis=1, keepdims=True)

    SALIDA.write_text(
        json.dumps(
            {"fragmentos": fragmentos, "vectores": matriz.tolist()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nListo: {SALIDA} ({SALIDA.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
