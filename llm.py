import os
import time
from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

load_dotenv()

MODELOS = [
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-flash-latest",
]

FORZADO = os.environ.get("MODELO_LLM")
if FORZADO:
    MODELOS = [FORZADO] + [m for m in MODELOS if m != FORZADO]

REINTENTABLES = (429, 503, 404)

MODELO_EMBEDDINGS = "gemini-embedding-001"

DIMENSIONES = 768

SEGURIDAD = [
    types.SafetySetting(category=categoria, threshold="BLOCK_MEDIUM_AND_ABOVE")
    for categoria in (
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    )
]


def cliente():
    llave = os.environ.get("GEMINI_API_KEY")
    if not llave:
        raise RuntimeError("Falta la variable de entorno GEMINI_API_KEY")
    return genai.Client(api_key=llave)


def _contenidos(turnos):
    contenidos = []
    for turno in turnos:
        papel = "user" if turno["rol"] == "user" else "model"
        contenidos.append(
            types.Content(role=papel, parts=[types.Part(text=turno["texto"])])
        )
    return contenidos


def _ajustes(instrucciones, esquemas=None):
    herramientas = None
    automatico = None
    if esquemas:
        herramientas = [types.Tool(function_declarations=esquemas)]
        # Las ejecuta quien pregunta, no yo.
        automatico = types.AutomaticFunctionCallingConfig(disable=True)

    return types.GenerateContentConfig(
        system_instruction=instrucciones,
        temperature=0.0,
        safety_settings=SEGURIDAD,
        tools=herramientas,
        automatic_function_calling=automatico,
    )

def responder(instrucciones, turnos):
    texto, _, modelo = _pedir(instrucciones, turnos, None)
    return texto, modelo

def responder_con_tools_del_cliente(instrucciones, turnos, tools):
    esquemas = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        esquemas.append(
            {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters")
                or {"type": "object", "properties": {}},
            }
        )

    if not esquemas:
        return responder(instrucciones, turnos)[0], []

    texto, llamadas, _ = _pedir(instrucciones, turnos, esquemas)
    return texto, llamadas


def _pedir(instrucciones, turnos, esquemas):
    conexion = cliente()
    contenidos = _contenidos(turnos)
    ajustes = _ajustes(instrucciones, esquemas)
    ultimo_error = None

    for modelo in MODELOS:
        try:
            respuesta = conexion.models.generate_content(
                model=modelo, contents=contenidos, config=ajustes
            )
            llamadas = [
                {"nombre": f.name, "argumentos": dict(f.args or {})}
                for f in (respuesta.function_calls or [])
            ]
            return respuesta.text or "", llamadas, modelo
        except errors.APIError as error:
            if error.code not in REINTENTABLES:
                raise
            ultimo_error = error

    raise RuntimeError(f"Ningun modelo disponible. Ultimo error: {ultimo_error}")

def embeber(textos, tipo="RETRIEVAL_DOCUMENT", por_lote=20):
    conexion = cliente()
    ajustes = types.EmbedContentConfig(
        output_dimensionality=DIMENSIONES, task_type=tipo
    )

    vectores = []
    for inicio in range(0, len(textos), por_lote):
        lote = textos[inicio : inicio + por_lote]

        for intento in range(4):
            try:
                respuesta = conexion.models.embed_content(
                    model=MODELO_EMBEDDINGS, contents=lote, config=ajustes
                )
                break
            except errors.APIError as error:
                if error.code != 429 or intento == 3:
                    raise
                time.sleep(10 * (intento + 1))

        vectores.extend(e.values for e in respuesta.embeddings)

    return vectores
