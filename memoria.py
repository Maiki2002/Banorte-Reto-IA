import json
import os
import time

LIMITE_TURNOS = 20         
CADUCIDAD = 60 * 60         # segundos sin actividad y la sesion se olvida

_PREFIJO = "cv-agent:sesion:"

# session_id 
_local = {}

_redis = None
_redis_listo = False


def _cliente():
    global _redis, _redis_listo
    if _redis_listo:
        return _redis

    url = os.environ.get("REDIS_URL")
    if url:
        import redis.asyncio as redis

        _redis = redis.from_url(url, decode_responses=True)
    _redis_listo = True
    return _redis


def _limpiar(turnos):
    charla = [t for t in turnos if t.get("rol") in ("user", "assistant")]
    return charla[-LIMITE_TURNOS:]


async def recuperar(sesion_id):
    if not sesion_id:
        return []

    cliente = _cliente()
    if cliente is not None:
        crudo = await cliente.get(_PREFIJO + sesion_id)
        return json.loads(crudo) if crudo else []

    entrada = _local.get(sesion_id)
    if not entrada or time.time() - entrada["visto"] > CADUCIDAD:
        return []
    return entrada["turnos"]


async def guardar(sesion_id, turnos):
    if not sesion_id:
        return

    recientes = _limpiar(turnos)

    cliente = _cliente()
    if cliente is not None:
        await cliente.set(
            _PREFIJO + sesion_id, json.dumps(recientes), ex=CADUCIDAD
        )
        return

    _local[sesion_id] = {"turnos": recientes, "visto": time.time()}
