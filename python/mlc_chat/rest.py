from .chat_module import ChatModule, quantization_keys

from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager
import uvicorn

import tvm

import argparse
import os
import json


session = {}


@asynccontextmanager
async def lifespan(app: FastAPI):

    ARGS = _parse_args()

    chat_mod = ChatModule(ARGS.device_name, ARGS.device_id)
    model_path = os.path.join(ARGS.artifact_path, ARGS.model + "-" + ARGS.quantization)
    model_dir = ARGS.model + "-" + ARGS.quantization
    model_lib = model_dir + "-" + ARGS.device_name + ".so"
    lib_dir = os.path.join(model_path, model_lib)
    prebuilt_lib_dir = os.path.join(ARGS.artifact_path, "prebuilt", "lib", model_lib)
    if os.path.exists(lib_dir):
        lib = tvm.runtime.load_module(lib_dir)
    elif os.path.exists(prebuilt_lib_dir):
        lib = tvm.runtime.load_module(prebuilt_lib_dir)
    else:
        raise ValueError(
            f"Unable to find {model_lib} at {lib_dir} or {prebuilt_lib_dir}."
        )

    local_model_path = os.path.join(model_path, "params")
    prebuilt_model_path = os.path.join(
        ARGS.artifact_path, "prebuilt", f"mlc-chat-{model_dir}"
    )
    if os.path.exists(local_model_path):
        chat_mod.reload(lib=lib, model_path=local_model_path)
    elif os.path.exists(prebuilt_model_path):
        chat_mod.reload(lib=lib, model_path=prebuilt_model_path)
    else:
        raise ValueError(
            f"Unable to find model params at {local_model_path} or {prebuilt_model_path}."
        )
    session["chat_mod"] = chat_mod

    yield

    session.clear()


app = FastAPI(lifespan=lifespan)


def _parse_args():
    args = argparse.ArgumentParser()
    args.add_argument(
        "--model", type=str, default="vicuna-v1-7b"
    )
    args.add_argument("--artifact-path", type=str, default="dist")
    args.add_argument(
        "--quantization",
        type=str,
        choices=quantization_keys(),
        default=quantization_keys()[0],
    )
    args.add_argument("--device-name", type=str, default="cuda")
    args.add_argument("--device-id", type=int, default=0)
    parsed = args.parse_args()
    return parsed


class ChatRequest(BaseModel):
    prompt: str
    stream: bool = False



@app.post("/chat/completions")
def request_completion(request: ChatRequest):
    """
    Creates model response for the given chat conversation.
    """
    session["chat_mod"].prefill(input=request.prompt)
    if request.stream:

        def iter_response():
            while not session["chat_mod"].stopped():
                session["chat_mod"].decode()
                msg = session["chat_mod"].get_message()
                yield json.dumps({"message": msg})

        return StreamingResponse(iter_response(), media_type="application/json")
    else:
        msg = None
        while not session["chat_mod"].stopped():
            session["chat_mod"].decode()
            msg = session["chat_mod"].get_message()
        return {"message": msg}


@app.post("/chat/reset")
def reset():
    """
    Reset the chat for the currently initialized model.
    """
    session["chat_mod"].reset_chat()


@app.get("/stats")
def read_stats():
    """
    Get the runtime stats.
    """
    return session["chat_mod"].runtime_stats_text()


if __name__ == "__main__":
    uvicorn.run("mlc_chat.rest:app", port=8000, reload=True, access_log=False)
