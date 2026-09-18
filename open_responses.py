import json
import time
import uuid

 # content parts
def leer_mensajes(body):
    entrada = body.get("input", "")

    if isinstance(entrada, str):
        return [{"rol": "user", "texto": entrada}]

    if not isinstance(entrada, list):
        return []

    mensajes = []
    for item in entrada:
        if not isinstance(item, dict):
            continue

        rol = item.get("role", "user")
        contenido = item.get("content")

        if isinstance(contenido, str):
            mensajes.append({"rol": rol, "texto": contenido})
        elif isinstance(contenido, list):
            partes = []
            for parte in contenido:
                if isinstance(parte, dict) and "text" in parte:
                    partes.append(parte["text"])
            mensajes.append({"rol": rol, "texto": " ".join(partes)})

    return mensajes

def leer_instrucciones(body, mensajes):
    partes = []
    if body.get("instructions"):
        partes.append(body["instructions"])
    for mensaje in mensajes:
        if mensaje["rol"] in ("system", "developer"):
            partes.append(mensaje["texto"])
    return "\n".join(partes)


def solo_conversacion(mensajes):
    return [m for m in mensajes if m["rol"] not in ("system", "developer")]


def ultima_pregunta(mensajes):
    for mensaje in reversed(mensajes):
        if mensaje["rol"] == "user":
            return mensaje["texto"]
    return ""


def armar_respuesta(modelo, texto, estado="completed", ident=None, anterior=None):
    ahora = int(time.time())
    terminada = estado == "completed"

    return {
        "id": ident or nuevo_id("resp"),
        "object": "response",
        "created_at": ahora,
        "completed_at": ahora if terminada else None,
        "status": estado,
        "incomplete_details": None,
        "model": modelo,
        "previous_response_id": anterior or None,
        "instructions": None,
        "output": [] if not terminada else [mensaje_de_salida(texto)],
        "error": None,
        "tools": [],
        "tool_choice": "auto",
        "truncation": "disabled",
        "parallel_tool_calls": False,
        "text": {"format": {"type": "text"}},
        "top_p": 1.0,
        "presence_penalty": 0.0,
        "frequency_penalty": 0.0,
        "top_logprobs": 0,
        "temperature": 0.0,
        "reasoning": None,
        "usage": None,
        "max_output_tokens": None,
        "max_tool_calls": None,
        "store": False,
        "background": False,
        "service_tier": "default",
        "metadata": {},
        "safety_identifier": None,
        "prompt_cache_key": None,
        "output_text": texto,
    }

 
def mensaje_de_salida(texto, ident=None, estado="completed"):
    return {
        "type": "message",
        "id": ident or nuevo_id("msg"),
        "status": estado,
        "role": "assistant",
        "phase": "final_answer",
        "content": [{"type": "output_text", "text": texto, "annotations": []}],
    }

def armar_respuesta_tool(modelo, llamadas):
    respuesta = armar_respuesta(modelo, "")
    respuesta["output"] = [
        {
            "type": "function_call",
            "id": nuevo_id("fc"),
            "call_id": nuevo_id("call"),
            "name": llamada["nombre"],
            "arguments": json.dumps(llamada["argumentos"], ensure_ascii=False),
            "status": "completed",
        }
        for llamada in llamadas
    ]
    respuesta["output_text"] = ""
    return respuesta

def error(codigo, mensaje, campo=None, http=400):
    return http, {
        "error": {
            "type": "invalid_request_error",
            "message": mensaje,
            "param": campo,
            "code": codigo,
        }
    }


def nuevo_id(prefijo):
    return prefijo + "_" + uuid.uuid4().hex


# SSE

def evento(tipo, numero, datos):
    cuerpo = {"type": tipo, "sequence_number": numero}
    cuerpo.update(datos)
    return f"event: {tipo}\ndata: {json.dumps(cuerpo, ensure_ascii=False)}\n\n"

class Transmision:
    def __init__(self, modelo, ident):
        self.modelo = modelo
        self.ident = ident
        self.item = nuevo_id("msg")
        self.numero = 0
        self.texto = ""

    def _siguiente(self):
        self.numero += 1
        return self.numero

    def abrir(self):
        vacia = armar_respuesta(
            self.modelo, "", estado="in_progress", ident=self.ident
        )
        eventos = [
            evento("response.created", self._siguiente(), {"response": vacia}),
            evento("response.in_progress", self._siguiente(), {"response": vacia}),
            evento(
                "response.output_item.added",
                self._siguiente(),
                {
                    "output_index": 0,
                    "item": mensaje_de_salida("", self.item, "in_progress"),
                },
            ),
            evento(
                "response.content_part.added",
                self._siguiente(),
                {
                    "item_id": self.item,
                    "output_index": 0,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                },
            ),
        ]
        return eventos

    def delta(self, trozo):
        self.texto += trozo
        return evento(
            "response.output_text.delta",
            self._siguiente(),
            {
                "item_id": self.item,
                "output_index": 0,
                "content_index": 0,
                "delta": trozo,
            },
        )

    def item_extra(self, item, indice=1):
        return eventos_de_item(self, item, indice)

    def cerrar(self):
        completa = armar_respuesta(self.modelo, self.texto, ident=self.ident)
        return [
            evento(
                "response.output_text.done",
                self._siguiente(),
                {
                    "item_id": self.item,
                    "output_index": 0,
                    "content_index": 0,
                    "text": self.texto,
                },
            ),
            evento(
                "response.content_part.done",
                self._siguiente(),
                {
                    "item_id": self.item,
                    "output_index": 0,
                    "content_index": 0,
                    "part": {
                        "type": "output_text",
                        "text": self.texto,
                        "annotations": [],
                    },
                },
            ),
            evento(
                "response.output_item.done",
                self._siguiente(),
                {"output_index": 0, "item": completa["output"][0]},
            ),
            evento("response.completed", self._siguiente(), {"response": completa}),
        ]


# Un item de output extra despues del mensaje, con sus eventos de
# apertura y cierre. Se usa para las tarjetas A2UI.
def eventos_de_item(transmision, item, indice=1):
    return [
        evento(
            "response.output_item.added",
            transmision._siguiente(),
            {"output_index": indice, "item": item},
        ),
        evento(
            "response.output_item.done",
            transmision._siguiente(),
            {"output_index": indice, "item": item},
        ),
    ]
