import json

import agente
import busqueda
import llm

CUANTAS = 5

INSTRUCCIONES = f"""
Preparas la pantalla de bienvenida de un agente que responde preguntas
sobre un perfil profesional ante reclutadores.

Te doy la informacion disponible y las herramientas del agente. Propon
{CUANTAS} preguntas que haria un reclutador y que el agente pueda
responder con eso.

Reglas:
- Cortas, menos de 45 caracteres, para que quepan en un boton.
- En espanol, impersonal o en tercera persona.
- Que entre todas se usen las tres herramientas, no solo una.
- Nada que no se pueda responder con la informacion disponible.
- Sin datos personales ni de contacto.

Responde solo con un JSON: {{"sugerencias": ["...", "..."]}}
"""


def resumen_del_indice():
    fragmentos, _ = busqueda.cargar()

    documentos = [f["texto"][:300] for f in fragmentos if f["fuente"] == "documentos"]
    repos = [f["titulo"] for f in fragmentos if f["fuente"] == "github"]
    nombres = "\n".join(f"- {h.__name__}: {h.__doc__.strip().splitlines()[0]}"
                        for h in agente.HERRAMIENTAS)

    return (
        "HERRAMIENTAS:\n" + nombres
        + "\n\nCV Y LINKEDIN:\n" + "\n".join(documentos)
        + "\n\nREPOSITORIOS:\n" + ", ".join(repos)
    )


def main():
    texto, modelo = llm.responder(
        INSTRUCCIONES, [{"rol": "user", "texto": resumen_del_indice()}]
    )
    limpio = texto.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    sugerencias = json.loads(limpio)["sugerencias"]

    print(f"Generadas con {modelo}:\n")
    for s in sugerencias:
        print(f"  {s}")
    print("\nPara pegar en promptSuggestions:")
    print(json.dumps(sugerencias, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
