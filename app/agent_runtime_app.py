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

import os
import logging

os.environ["GOOGLE_API_USE_MTLS"] = "never"
os.environ["GOOGLE_API_USE_CLIENT_CERTIFICATE"] = "false"

import vertexai
from dotenv import load_dotenv
from vertexai.agent_engines.templates.adk import AdkApp

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
