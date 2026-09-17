import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

import agente
import llm
import memoria
import open_responses as spec
import reglas

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MENSAJE_ERROR = (
    "Ahora mismo no puedo responder por un problema tecnico. "
    "¿Puedes intentarlo de nuevo en un momento?"
)


@app.post("/v1/responses")
async def responder(request: Request):
    body = await request.json()

    todos = spec.leer_mensajes(body)
    instrucciones = spec.leer_instrucciones(body, todos)
    mensajes = spec.solo_conversacion(todos)
    pregunta = spec.ultima_pregunta(mensajes)
    modelo = body.get("model", "cv-agent")
    print("PREGUNTA:", pregunta)

    if body.get("tools"):
        base = instrucciones or "Eres un asistente util."
        texto, llamadas = llm.responder_con_tools_del_cliente(
            base, mensajes, body["tools"]
        )
        if llamadas:
            return spec.armar_respuesta_tool(modelo, llamadas)
        return spec.armar_respuesta(modelo, texto)

    rechazo = reglas.revisar(pregunta)
    if rechazo:
        if body.get("stream"):
            return StreamingResponse(
                transmitir_fijo(modelo, rechazo), media_type="text/event-stream"
            )
        return spec.armar_respuesta(modelo, rechazo)

    # previous_response_id
    anterior = body.get("previous_response_id") or ""
    previos = []
    if anterior:
        previos = await memoria.recuperar(anterior)
        if not previos:
            http, cuerpo = spec.error(
                "previous_response_not_found",
                f"No existe la respuesta previa '{anterior}'. Puede haber "
                "caducado o no se guardo con store:true.",
                "previous_response_id",
                404,
            )
            return JSONResponse(status_code=http, content=cuerpo)

    conversacion = previos + mensajes

    ident = spec.nuevo_id("resp")
    guardar = bool(body.get("store"))

    if body.get("stream"):
        return StreamingResponse(
            transmitir(modelo, conversacion, ident, guardar, instrucciones),
            media_type="text/event-stream",
        )

    try:
        texto, usadas = await agente.responder(conversacion, extra=instrucciones)
        print("HERRAMIENTAS:", usadas or "ninguna")
    except Exception as error:
        print("ERROR del modelo:", error)
        return spec.armar_respuesta(modelo, MENSAJE_ERROR)

    if guardar:
        await memoria.guardar(
            ident, conversacion + [{"rol": "assistant", "texto": texto}]
        )

    return spec.armar_respuesta(modelo, texto, ident=ident, anterior=anterior)


async def transmitir_fijo(modelo, texto):
    envio = spec.Transmision(modelo, spec.nuevo_id("resp"))
    for e in envio.abrir():
        yield e
    yield envio.delta(texto)
    for e in envio.cerrar():
        yield e

async def transmitir(modelo, conversacion, ident, guardar, instrucciones=None):
    envio = spec.Transmision(modelo, ident)
    for e in envio.abrir():
        yield e

    usadas = []
    try:
        async for tipo, dato in agente.responder_en_partes(
            conversacion, extra=instrucciones
        ):
            if tipo == "herramienta":
                usadas.append(dato)
            else:
                yield envio.delta(dato)
    except Exception as error:
        print("ERROR del modelo:", error)
        yield envio.delta(MENSAJE_ERROR)

    print("HERRAMIENTAS:", usadas or "ninguna")

    for e in envio.cerrar():
        yield e

    if guardar:
        await memoria.guardar(
            ident, conversacion + [{"rol": "assistant", "texto": envio.texto}]
        )


@app.post("/responses/compact")
@app.post("/v1/responses/compact")
async def compactar(request: Request):
    body = await request.json()

    if not body.get("model"):
        http, cuerpo = spec.error(
            "missing_required_parameter",
            "El campo 'model' es obligatorio.",
            "model",
        )
        return JSONResponse(status_code=http, content=cuerpo)

    conversacion = spec.solo_conversacion(spec.leer_mensajes(body))
    texto = "\n".join(f"{m['rol']}: {m['texto']}" for m in conversacion)
    resumen = texto[:2000]

    if conversacion:
        try:
            resumen, _ = llm.responder(
                "Resume la siguiente conversacion en un parrafo, conservando "
                "los datos concretos que se mencionaron. Responde solo con "
                "el resumen.",
                [{"rol": "user", "texto": texto}],
            )
        except Exception as error:
            print("ERROR al compactar:", error)

    return {
        "id": spec.nuevo_id("cmpt"),
        "object": "response.compaction",
        "created_at": int(time.time()),
        "output": [
            {
                "type": "compaction",
                "id": spec.nuevo_id("cmp"),
                "encrypted_content": resumen,
                "created_by": body["model"],
            }
        ],
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }

@app.get("/health")
async def health():
    return {"status": "ok"}
