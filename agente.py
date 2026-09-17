import datetime
import os
import re
import uuid
from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import errors, types

import busqueda
import reglas

load_dotenv()

if not os.environ.get("GOOGLE_API_KEY"):
    os.environ["GOOGLE_API_KEY"] = os.environ.get("GEMINI_API_KEY", "")

APP = "cv-agent"

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

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

# herramientas


def buscar_cv(consulta: str) -> str:
    """Busca en el CV, el perfil de LinkedIn y las notas del candidato.

    Usala para experiencia laboral, formacion academica, certificaciones,
    idiomas o habilidades declaradas.

    Args:
        consulta: que buscar, en palabras clave o pregunta corta.
    """
    encontrados = busqueda.buscar(consulta, fuente="documentos", cuantos=5)
    if not encontrados:
        return "No encontre nada sobre eso en los documentos del candidato."
    return con_su_fuente(encontrados)


def buscar_github(consulta: str) -> str:
    encontrados = busqueda.buscar(consulta, fuente="github", cuantos=4)
    if not encontrados:
        return "No encontre ningun repositorio relacionado con eso."
    return con_su_fuente(encontrados)


def calcular_experiencia() -> str:
    fragmentos, _ = busqueda.cargar()
    texto = "\n".join(
        f["texto"] for f in fragmentos if f["fuente"] == "documentos"
    )

    periodos = periodos_del_texto(texto)
    if not periodos:
        return "No encontre fechas de experiencia laboral en los documentos."

    nombre = nombre_del_candidato(texto)

    unidos = fusionar(periodos)
    meses = sum((h.year - d.year) * 12 + (h.month - d.month) for d, h in unidos)
    detalle = ", ".join(
        f"{d.strftime('%m/%Y')} a {h.strftime('%m/%Y')}" for d, h in unidos
    )

    aviso = ""
    if len(periodos) > len(unidos):
        aviso = (
            f" De {len(periodos)} periodos encontrados, "
            f"{len(periodos) - len(unidos)} se encimaban y se unieron para no "
            "contar el mismo tiempo dos veces."
        )

    return (
        f"Candidato: {nombre}. Experiencia calculada con las fechas de los "
        f"documentos: {meses} meses ({meses // 12} anios y {meses % 12} meses). "
        f"Periodos: {detalle}.{aviso}"
    )


def nombre_del_candidato(texto):
    for linea in texto.splitlines():
        linea = linea.strip()
        palabras = linea.split()
        if 2 <= len(palabras) <= 5 and linea[:1].isupper() and "@" not in linea:
            return linea
    return "no identificado en los documentos"


HERRAMIENTAS = [buscar_cv, buscar_github, calcular_experiencia]


def con_su_fuente(encontrados):
    partes = []
    for r in encontrados:
        partes.append(
            f"[fuente: {r['fuente']}/{r['titulo']} | similitud {r['similitud']}]\n"
            f"{r['texto']}"
        )
    return "\n\n".join(partes)

def periodos_del_texto(texto):
    hoy = datetime.date.today()
    periodos = []

    patron = r"(\d{1,2})/(\d{4})\s*[-–—]\s*(\d{1,2}/\d{4}|Present|Actualidad|Actual)"
    for mes, anio, fin in re.findall(patron, texto, re.IGNORECASE):
        desde = datetime.date(int(anio), int(mes), 1)
        if fin.lower() in ("present", "actualidad", "actual"):
            hasta = hoy
        else:
            m, a = fin.split("/")
            hasta = datetime.date(int(a), int(m), 1)
        periodos.append((desde, hasta))

    patron = r"(\w+) de (\d{4})\s*[-–—]\s*(Present|Actualidad|\w+ de \d{4})"
    for nombre_mes, anio, fin in re.findall(patron, texto, re.IGNORECASE):
        mes = MESES.get(nombre_mes.lower())
        if not mes:
            continue
        desde = datetime.date(int(anio), mes, 1)
        if fin.lower() in ("present", "actualidad"):
            hasta = hoy
        else:
            nombre, a = fin.split(" de ")
            hasta = datetime.date(int(a), MESES.get(nombre.lower(), 1), 1)
        periodos.append((desde, hasta))

    return [(d, h) for d, h in periodos if h > d]


def fusionar(periodos):
    if not periodos:
        return []

    periodos = sorted(periodos)
    unidos = [periodos[0]]

    for desde, hasta in periodos[1:]:
        ultimo_desde, ultimo_hasta = unidos[-1]
        if desde <= ultimo_hasta:
            unidos[-1] = (ultimo_desde, max(ultimo_hasta, hasta))
        else:
            unidos.append((desde, hasta))

    return unidos


# prompt

ESTILO = """
Como escribes:

- Abre con una o dos frases en prosa que respondan directamente lo que
  preguntaron. Nunca arranques con una lista.
- Despues desarrolla: explica que hizo, con que tecnologia y para que
  servia. Si lo recuperado menciona un resultado o un problema resuelto,
  cuentalo, porque es lo que le interesa a quien contrata.
- Conecta lo que recuperaste. Si una tecnologia aparece en un empleo y en
  un proyecto, dilo.
- Usa vinetas solo para cosas paralelas (un stack, varios repositorios).
  Para lo demas, escribe parrafos.
- Apunta a unas 120 o 200 palabras. Si de verdad hay poco que contar, se
  breve en vez de rellenar.
- Habla del candidato en tercera persona. Usa su nombre SOLO si
  aparece en lo que recuperaste; si no aparece, di "el candidato".
  Nunca inventes un nombre.

Como cierras:

Termina ofreciendo dos o tres preguntas concretas para seguir, una por
linea. Tienen que ser sobre temas de los que acabas de ver informacion y
distintas de la que ya te hicieron.
"""

INSTRUCCIONES = (
    "Eres el agente que representa el perfil profesional de un candidato "
    "ante reclutadores. Respondes en espanol, en tono profesional y cercano.\n"
    + reglas.POLITICAS
    + ESTILO
    + "\nTodo lo que cuentes sale de lo que te devuelvan las herramientas. "
    "Puedes explicarlo y ordenarlo con tus palabras, pero no agregues datos "
    "que no viste. Si lo recuperado no responde la pregunta, dilo con "
    "claridad en vez de improvisar. Los nombres propios (universidades, "
    "empresas, tecnologias, repositorios) los copias exactamente como "
    "aparecen."
)


# agente

_sesiones = InMemorySessionService()
_runners = {}


def _runner(modelo, extra):
    clave = (modelo, extra or "")
    if clave in _runners:
        return _runners[clave]

    texto = INSTRUCCIONES
    if extra:
        texto += "\n\nIndicaciones adicionales de quien pregunta:\n" + extra

    _runners[clave] = Runner(
        app_name=APP,
        agent=LlmAgent(
            name="agente_cv",
            model=modelo,
            description="Responde sobre el perfil profesional de un candidato.",
            instruction=texto,
            tools=HERRAMIENTAS,
        ),
        session_service=_sesiones,
    )
    return _runners[clave]

async def _sesion_con_historial(turnos):
    sesion_id = "s_" + uuid.uuid4().hex
    sesion = await _sesiones.create_session(
        app_name=APP, user_id="u", session_id=sesion_id
    )

    for turno in turnos[:-1]:
        papel = "user" if turno["rol"] == "user" else "model"
        autor = "user" if turno["rol"] == "user" else "agente_cv"
        await _sesiones.append_event(
            session=sesion,
            event=Event(
                author=autor,
                content=types.Content(
                    role=papel, parts=[types.Part(text=turno["texto"])]
                ),
            ),
        )

    return sesion_id


def _mensaje(turnos):
    for turno in reversed(turnos):
        if turno["rol"] == "user":
            return types.Content(
                role="user", parts=[types.Part(text=turno["texto"])]
            )
    return types.Content(role="user", parts=[types.Part(text="")])


async def responder(turnos, extra=None):
    mensaje = _mensaje(turnos)
    ultimo_error = None

    for modelo in MODELOS:
        try:
            sesion_id = await _sesion_con_historial(turnos)
            texto = ""
            usadas = []

            async for evento in _runner(modelo, extra).run_async(
                user_id="u", session_id=sesion_id, new_message=mensaje
            ):
                for llamada in evento.get_function_calls() or []:
                    usadas.append(llamada.name)
                if evento.is_final_response() and evento.content:
                    for parte in evento.content.parts or []:
                        if parte.text:
                            texto += parte.text

            return texto, usadas
        except errors.APIError as error:
            if error.code not in REINTENTABLES:
                raise
            print(f"  ({modelo} no disponible: {error.code}, pruebo el siguiente)")
            ultimo_error = error

    raise RuntimeError(f"Ningun modelo disponible. Ultimo error: {ultimo_error}")

async def responder_en_partes(turnos, extra=None):
    mensaje = _mensaje(turnos)
    ajustes = RunConfig(streaming_mode=StreamingMode.SSE)
    ultimo_error = None

    for modelo in MODELOS:
        emitido = False
        try:
            sesion_id = await _sesion_con_historial(turnos)
            enviado = ""

            async for evento in _runner(modelo, extra).run_async(
                user_id="u",
                session_id=sesion_id,
                new_message=mensaje,
                run_config=ajustes,
            ):
                for llamada in evento.get_function_calls() or []:
                    yield ("herramienta", llamada.name)

                if not evento.content or not evento.content.parts:
                    continue

                trozo = "".join(p.text for p in evento.content.parts if p.text)
                if not trozo:
                    continue

                # SSE el ADK manda los parciales y despues el texto
    
                if evento.partial:
                    enviado += trozo
                    emitido = True
                    yield ("delta", trozo)
                elif evento.is_final_response() and trozo != enviado:
                    resto = trozo[len(enviado):] if trozo.startswith(enviado) else trozo
                    if resto:
                        enviado += resto
                        emitido = True
                        yield ("delta", resto)
            return
        except errors.APIError as error:
            if error.code not in REINTENTABLES or emitido:
                raise
            print(f"  ({modelo} no disponible: {error.code}, pruebo el siguiente)")
            ultimo_error = error

    raise RuntimeError(f"Ningun modelo disponible. Ultimo error: {ultimo_error}")
