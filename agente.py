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
import embeddings
import tarjetas
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

    # Si no hay nada indexado que sirva, puede que sean repos nuevos:
    # los traigo de GitHub, los indexo y vuelvo a buscar.
    if not encontrados and indexar_repos_nuevos():
        encontrados = busqueda.buscar(consulta, fuente="github", cuantos=4)

    if not encontrados:
        return "No encontre ningun repositorio relacionado con eso."
    return con_su_fuente(encontrados)


def buscar_todo(consulta: str) -> str:
    """Busca a la vez en los documentos y en los repositorios.

    Usala cuando la pregunta sea amplia o cuando quieras contrastar lo
    que dice el CV con lo que hay en el codigo: por ejemplo, si el CV
    menciona una tecnologia, ver si existe un repositorio que la use.

    Args:
        consulta: que buscar, en palabras clave o pregunta corta.
    """
    docs = busqueda.buscar(consulta, fuente="documentos", cuantos=4)
    repos = busqueda.buscar(consulta, fuente="github", cuantos=3)

    if not docs and not repos and indexar_repos_nuevos():
        repos = busqueda.buscar(consulta, fuente="github", cuantos=3)

    encontrados = docs + repos
    if not encontrados:
        return "No encontre nada sobre eso en ninguna fuente."
    return con_su_fuente(encontrados)


def calcular_experiencia() -> str:
    texto = "\n".join(embeddings.textos_de("documentos"))

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



# Consulta GitHub en vivo y agrega al indice los repos que todavia no
# estuvieran. Devuelve cuantos agrego.
def indexar_repos_nuevos():
    import indexar

    try:
        ya_estan = embeddings.titulos("github")
        nuevos = [
            f for f in indexar.desde_github() if f["titulo"] not in ya_estan
        ]
    except Exception as error:
        print("No pude consultar GitHub:", error)
        return 0

    if not nuevos:
        return 0

    print(f"  indexando {len(nuevos)} repos nuevos:",
          ", ".join(f["titulo"] for f in nuevos))
    embeddings.agregar(nuevos)
    return len(nuevos)


def mostrar_tarjetas(titulo: str) -> str:
    """Presenta en tarjetas visuales lo ultimo que buscaste.

    Usala cuando la respuesta enumere varios elementos comparables entre
    si (proyectos, repositorios, empleos): las tarjetas se leen mejor que
    una lista larga. NO la uses para una cifra, un dato suelto o una
    explicacion en prosa.

    Si la llamas, tu texto debe ser breve y presentar el conjunto, sin
    repetir lo que ya va en cada tarjeta.

    Args:
        titulo: encabezado corto para el grupo de tarjetas.
    """
    if not tarjetas.activado():
        return "Las tarjetas no estan disponibles; responde solo con texto."
    if not ultimos:
        return "No hay resultados recientes que mostrar en tarjetas."

    pedidas["titulo"] = titulo
    return f"Listo: se mostraran {len(ultimos)} tarjetas bajo '{titulo}'."


# El modelo pide las tarjetas llamando a la herramienta; main.py las
# adjunta solo si quedo algo aqui.
pedidas = {}


def mostrar_presentacion() -> str:
    """Muestra la carta de presentacion del candidato.

    Incluye su nombre, a que se dedica, sus cifras principales y botones
    para empezar la conversacion. Usala en el primer mensaje, cuando
    saluden, cuando pregunten quien es o que puede contar este agente.
    """
    if not tarjetas.activado():
        return "La vista no esta disponible; responde solo con texto."
    pedidas["presentacion"] = True
    return "Listo: se mostrara la carta de presentacion."


def mostrar_habilidades(areas: str) -> str:
    """Muestra las habilidades por area, con barras de nivel e iconos.

    Args:
        areas: las areas y sus tecnologias, una por linea, con el formato
            "Area: tecnologia, tecnologia". Usa solo lo que hayas visto
            en lo recuperado.
    """
    if not tarjetas.activado():
        return "La vista no esta disponible; responde solo con texto."
    pedidas["habilidades"] = areas
    return "Listo: se mostraran las habilidades por area."


def mostrar_trayectoria() -> str:
    """Muestra la experiencia y los proyectos como una linea de tiempo.

    Cada periodo aparece con sus fechas en una tarjeta ordenada. Usala
    cuando pregunten por la experiencia laboral, la trayectoria o el
    recorrido profesional.
    """
    if not tarjetas.activado():
        return "La vista no esta disponible; responde solo con texto."
    pedidas["trayectoria"] = True
    return "Listo: se mostrara la linea de tiempo."


def mostrar_stack(areas: str) -> str:
    """Muestra el stack tecnico agrupado por area, con una grafica.

    Usala cuando pregunten por tecnologias, herramientas o que sabe usar.

    Args:
        areas: las areas y sus tecnologias, una por linea, con el formato
            "Area: tecnologia, tecnologia, tecnologia". Por ejemplo:
            "Backend: Python, FastAPI, Java\nFrontend: React, Vite".
            Usa solo tecnologias que hayas visto en lo recuperado.
    """
    if not tarjetas.activado():
        return "La vista no esta disponible; responde solo con texto."
    pedidas["stack"] = areas
    return "Listo: se mostrara el stack agrupado por area."


def mostrar_panel() -> str:
    """Muestra un panel visual con las cifras del perfil y una grafica.

    Incluye meses de experiencia, numero de repositorios y lenguajes
    distintos, mas una grafica de repositorios por lenguaje. Todas las
    cifras son calculadas, no estimadas.

    Usala cuando pidan un resumen, un panorama o una vista general del
    perfil. Si la llamas, tu texto debe ser corto: el panel muestra los
    numeros, tu pones el contexto.
    """
    if not tarjetas.activado():
        return "El panel no esta disponible; responde solo con texto."

    pedidas["panel"] = True
    return "Listo: se mostrara el panel con las cifras del perfil."


HERRAMIENTAS = [buscar_cv, buscar_github, buscar_todo, calcular_experiencia]
if tarjetas.activado():
    HERRAMIENTAS.extend(
        [
            mostrar_presentacion,
            mostrar_tarjetas,
            mostrar_panel,
            mostrar_trayectoria,
            mostrar_stack,
            mostrar_habilidades,
        ]
    )


# Lo ultimo que se recupero, por si hay que armar tarjetas con ello.
ultimos = []


def con_su_fuente(encontrados):
    # Se guarda lo recuperado por si hay que armar tarjetas con ello.
    ultimos.clear()
    ultimos.extend(encontrados)

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
Tu postura:

No eres un buscador de datos: representas al candidato ante alguien que
decide si lo entrevista. Tu trabajo es que entienda por que vale la pena,
usando lo que de verdad hizo.

Como escribes:

- Abre con una o dos frases en prosa que respondan lo que preguntaron y
  ya digan algo que valga. Nunca arranques con una lista.
- Cuenta el impacto, no la tarea. En vez de "uso Python", di que
  problema resolvio, con que y para que servia. Si lo recuperado
  menciona un resultado, una migracion o un sistema heredado que tuvo
  que entender, eso es lo que interesa.
- Conecta lo que recuperaste. Si una tecnologia aparece en un empleo y
  en un proyecto propio, dilo: demuestra que no la vio una sola vez.
- Busca en mas de un sitio antes de responder algo amplio. Para
  preguntas generales usa buscar_todo, o llama a buscar_cv y a
  buscar_github por separado: una respuesta que cruza el CV con los
  repositorios convence mas que una que solo mira una fuente.
- Cuando algo sea pequeno, enmarcalo bien en vez de agrandarlo. Un
  becario que documenta un servicio SOAP sin documentacion previa
  demuestra algo real; no hace falta llamarlo arquitecto.
- Usa vinetas solo para cosas paralelas (un stack, varios repositorios).
  Para lo demas, escribe parrafos.
- Apunta a unas 120 o 200 palabras.
- Habla del candidato en tercera persona. Usa su nombre SOLO si aparece
  en lo que recuperaste; si no, di "el candidato". Nunca inventes un
  nombre.

Los limites, que no se negocian:

- Solo afirmas lo que viste en lo recuperado. Puedes explicarlo, ordenarlo
  y darle contexto, pero no agregar experiencia, anios, niveles ni
  tecnologias que no aparezcan.
- Nada de superlativos vacios: "experto", "dominio total", "el mejor".
  Un dato concreto convence mas que un adjetivo.
- Si te preguntan por algo que no tiene, dilo con naturalidad y ofrece lo
  mas cercano que si tenga. Reconocer un hueco da mas credibilidad que
  esquivarlo.

Como lo presentas:

Tienes vistas visuales y casi siempre hay una que encaja. Elige:

- mostrar_presentacion  quien es: nombre, cifras y botones de inicio.
  Usala en el primer mensaje y cuando saluden.
- mostrar_habilidades   habilidades por area, con barras de nivel
- mostrar_tarjetas   proyectos o repositorios, una tarjeta cada uno
- mostrar_trayectoria experiencia laboral o recorrido, en linea de tiempo
- mostrar_stack      tecnologias agrupadas por area, con grafica
- mostrar_panel      resumen general: cifras del perfil y grafica

Usa la que corresponda siempre que la respuesta tenga varios elementos o
cifras. Solo responde con texto pelado cuando sea un dato muy suelto.
Ante un saludo o un "quien eres", usa mostrar_presentacion.

Puedes usar emoji en los titulos y etiquetas cuando ayuden a leer de un
vistazo, con moderacion: 💻 backend, 🧠 IA, 🎯 objetivos, ⏳ tiempo.

Si llamas a una de esas vistas, tu texto se acorta: presenta el conjunto
en dos o tres frases y deja que lo visual cuente el detalle. No repitas
lo que ya va ahi.

Las preguntas con las que cierras se convierten en botones, asi que
escribelas cortas y concretas: quien pregunta va a hacer clic en ellas.

Como cierras:

Termina ofreciendo dos o tres preguntas concretas para seguir, una por
linea. Que inviten a profundizar en lo mas fuerte de lo que acabas de
contar, y sean sobre temas de los que viste informacion.
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
    pedidas.clear()
    ultimos.clear()
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
    pedidas.clear()
    ultimos.clear()
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


# Las preguntas con las que cierra la respuesta se convierten en botones.
def preguntas_de(texto):
    sueltas = []
    for linea in texto.splitlines():
        linea = linea.strip(" -*\u2022")
        if linea.endswith("?") and 12 < len(linea) < 80:
            sueltas.append(linea)
    return sueltas[-3:]


# Arma los mensajes A2UI segun lo que haya pedido el agente.
def mensajes_a2ui(respuesta=""):
    seguir = preguntas_de(respuesta)

    if pedidas.get("presentacion"):
        fragmentos, _ = embeddings.todos()
        texto = "\n".join(embeddings.textos_de("documentos"))
        return tarjetas.presentacion(
            nombre_del_candidato(texto),
            "💻 Desarrollador Full Stack · 🧠 Integración de IA",
            meses_trabajados(),
            sum(1 for f in fragmentos if f.get("fuente") == "github"),
            len(tarjetas.lenguajes_de(fragmentos)),
            preguntas=seguir or [
                "¿Qué proyectos tiene en GitHub?",
                "¿Cuál es su trayectoria laboral?",
                "¿Qué tecnologías maneja?",
            ],
        )

    if pedidas.get("habilidades"):
        areas = _areas_de(pedidas["habilidades"])
        if areas:
            return tarjetas.habilidades(areas, preguntas=seguir)

    if pedidas.get("panel"):
        fragmentos, _ = embeddings.todos()
        return tarjetas.panel(meses_trabajados(), fragmentos, preguntas=seguir)

    if pedidas.get("trayectoria"):
        texto = "\n".join(embeddings.textos_de("documentos"))
        periodos = [
            (d.strftime("%m/%Y"), h.strftime("%m/%Y"), "Periodo registrado")
            for d, h in fusionar(periodos_del_texto(texto))
        ]
        return tarjetas.linea_de_tiempo(periodos, preguntas=seguir)

    if pedidas.get("stack"):
        areas = _areas_de(pedidas["stack"])
        if areas:
            return tarjetas.stack(areas, preguntas=seguir)

    return tarjetas.desde_fragmentos(
        ultimos, pedidas.get("titulo", "Perfil"), preguntas=seguir
    )


def meses_trabajados():
    texto = "\n".join(embeddings.textos_de("documentos"))
    periodos = fusionar(periodos_del_texto(texto))
    return sum((h.year - d.year) * 12 + (h.month - d.month) for d, h in periodos)


# "Backend: Python, Java" por linea, a pares (area, tecnologias).
def _areas_de(texto):
    areas = []
    for linea in texto.splitlines():
        if ":" not in linea:
            continue
        area, tecnologias = linea.split(":", 1)
        lista = [t.strip() for t in tecnologias.split(",") if t.strip()]
        if lista:
            areas.append((area.strip(), lista))
    return areas
