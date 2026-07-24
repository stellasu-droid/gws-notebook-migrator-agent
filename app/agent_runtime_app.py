# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import inspect
import json
import os
import logging
from fastapi import FastAPI, HTTPException, Request, encoders, responses

os.environ["GOOGLE_API_USE_MTLS"] = "never"
os.environ["GOOGLE_API_USE_CLIENT_CERTIFICATE"] = "false"

import vertexai
from dotenv import load_dotenv
from vertexai.agent_engines.templates.adk import AdkApp
from google.adk.cli.fast_api import get_fast_api_app

from app.agent import app as adk_app

load_dotenv()

class AgentEngineApp(AdkApp):
    def set_up(self) -> None:
        """Initialize the agent engine app."""
        vertexai.init()
        super().set_up()
        logging.basicConfig(level=logging.INFO)

gemini_location = os.environ.get("GOOGLE_CLOUD_LOCATION")
agent_runtime = AgentEngineApp(
    app=adk_app,
)

# Expose ASGI FastAPI application for container deployment (uvicorn server entrypoint)
AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
)

def _no_op_instrumentor_builder(project_id: str) -> None:
    return None

runtime_instance: AdkApp | None = None

def get_runtime() -> AdkApp:
    global runtime_instance
    if runtime_instance is None:
        runtime_instance = AgentEngineApp(
            app=adk_app,
            instrumentor_builder=_no_op_instrumentor_builder,
        )
        runtime_instance.set_up()
    return runtime_instance

@app.post("/api/stream_reasoning_engine")
async def stream_query_endpoint(request: Request) -> responses.StreamingResponse:
    body = await request.json()
    rt = get_runtime()
    class_method = body.get("class_method", "stream_query")
    method = getattr(rt, class_method)

    async def generator():
        async for event in method(**(body.get("input") or {})):
            yield json.dumps(event) + "\n"

    return responses.StreamingResponse(
        content=generator(), media_type="application/json"
    )

@app.post("/api/reasoning_engine")
async def query_endpoint(request: Request) -> responses.JSONResponse:
    body = await request.json()
    rt = get_runtime()
    class_method = body.get("class_method", "query")
    method = getattr(rt, class_method)
    kwargs = body.get("input") or {}
    output = (
        await method(**kwargs)
        if inspect.iscoroutinefunction(method)
        else method(**kwargs)
    )
    return responses.JSONResponse(
        content=encoders.jsonable_encoder({"output": output})
    )
