# main.py (FastAPI Agent)
import io
from contextlib import redirect_stdout, redirect_stderr
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
# preload Petex modules
import petex_client.gap as gap
import petex_client.resolve as resolve
from petex_client.server import PetexServer

app = FastAPI(title="Workflow Agent", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or ["http://localhost:3000"] if you want to be strict
    allow_credentials=True,
    allow_methods=["*"],   # include OPTIONS, POST, GET etc
    allow_headers=["*"],
)
import logging

logger = logging.getLogger("workflow_agent")

_srv_instance = None

def get_srv():
    """Return a live PetexServer instance, reconnecting if necessary."""
    global _srv_instance

    try:
        if _srv_instance is None:
            logger.info("Creating new PetexServer COM session...")
            _srv_instance = PetexServer()
            _srv_instance.__enter__()
        else:
            # cheap probe
            if not hasattr(_srv_instance, "_server") or _srv_instance._server is None:
                raise Exception("COM not initialized")
            _ = _srv_instance._server.GetTypeInfoCount()
    except Exception as e:
        logger.error(f"PetexServer connection lost, reconnecting: {e}")
        try:
            if _srv_instance:
                _srv_instance.close()
        except Exception:
            pass
        _srv_instance = PetexServer()
        _srv_instance.__enter__()

    return _srv_instance


# 🔹 Persistent global context (Jupyter-like kernel)
GLOBAL_CONTEXT = {
    "gap": gap,
    "resolve": resolve,
    "PetexServer": PetexServer,
    # srv will be injected per execution
}

@app.exception_handler(Exception)
async def all_exceptions_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": str(exc)},
    )

@app.post("/run_cell/")
async def run_cell(request: Request):
    data = await request.json()
    code = data.get("code", "")

    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()

    try:
        GLOBAL_CONTEXT["srv"] = get_srv()
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            exec(code, GLOBAL_CONTEXT)
    except Exception as e:
        stderr_buf.write(f"{type(e).__name__}: {e}\n")

    vars_snapshot = {
        k: {"type": type(v).__name__, "preview": str(v)[:50]}
        for k, v in GLOBAL_CONTEXT.items()
        if not k.startswith("__")
        and not callable(v)
        and not isinstance(v, type)
        and k not in {"gap", "resolve", "PetexServer", "srv"}
    }

    return JSONResponse({
        "stdout": stdout_buf.getvalue(),
        "stderr": stderr_buf.getvalue(),
        "variables": vars_snapshot,
    })


@app.post("/run_all/")
async def run_all(request: Request):
    data = await request.json()
    cells = data.get("cells", [])

    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()

    try:
        GLOBAL_CONTEXT["srv"] = get_srv()
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            for code in cells:
                exec(code, GLOBAL_CONTEXT)
    except Exception as e:
        stderr_buf.write(f"{type(e).__name__}: {e}\n")

    vars_snapshot = {
        k: {"type": type(v).__name__, "preview": str(v)[:50]}
        for k, v in GLOBAL_CONTEXT.items()
        if not k.startswith("__")
        and not callable(v)
        and not isinstance(v, type)
        and k not in {"gap", "resolve", "PetexServer", "srv"}
    }

    return JSONResponse({
        "stdout": stdout_buf.getvalue(),
        "stderr": stderr_buf.getvalue(),
        "variables": vars_snapshot,
    })


@app.get("/variables/")
async def list_variables():
    reserved = {"gap", "resolve", "PetexServer", "srv"}
    result = {}

    for k, v in GLOBAL_CONTEXT.items():
        if (
            k.startswith("__")
            or k in reserved
            or callable(v)
            or isinstance(v, type)
        ):
            continue

        try:
            t = type(v).__name__
            preview = str(v)
            if len(preview) > 80:
                preview = preview[:77] + "..."
            result[k] = {"type": t, "preview": preview}
        except Exception:
            result[k] = {"type": "unknown", "preview": ""}

    return JSONResponse(result)


@app.post("/reset_context/")
async def reset_context():
    GLOBAL_CONTEXT.clear()
    GLOBAL_CONTEXT.update({
        "gap": gap,
        "resolve": resolve,
        "PetexServer": PetexServer,
        "srv": get_srv(),
    })
    return JSONResponse({"status": "reset"})


@app.post("/delete_var/")
async def delete_var(request: Request):
    data = await request.json()
    name = data.get("name")
    if name and name in GLOBAL_CONTEXT:
        del GLOBAL_CONTEXT[name]
    return JSONResponse({"status": "ok", "deleted": name})


@app.post("/set_var/")
async def set_var(request: Request):
    data = await request.json()
    name = data.get("name")
    value = data.get("value")
    vtype = data.get("type", "str")

    try:
        if vtype == "int":
            value = int(value)
        elif vtype == "float":
            value = float(value)
        elif vtype == "bool":
            value = str(value).lower() in ("1", "true", "yes")
        else:
            value = str(value)

        GLOBAL_CONTEXT[name] = value
        return JSONResponse({"status": "ok", "name": name, "value": value})
    except Exception as e:
        return JSONResponse({"status": "error", "msg": str(e)}, status=400)
