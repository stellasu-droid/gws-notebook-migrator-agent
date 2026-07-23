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
import re
import logging
from typing import Optional
from google.adk import Context

try:
    from google.adk.integrations.agent_identity import GcpAuthProvider, GcpAuthProviderScheme
except ImportError:
    GcpAuthProvider = None
    GcpAuthProviderScheme = None

try:
    from google.adk.integrations.agent_registry import AgentRegistry
except ImportError:
    AgentRegistry = None

logger = logging.getLogger("notebook_migrator.utils")
logger.setLevel(logging.INFO)

def get_agent_registry_client(project_id: str, location: str = "us-central1") -> Optional[object]:
    """Returns an ADK AgentRegistry client instance if available."""
    if AgentRegistry is not None:
        try:
            return AgentRegistry(project_id=project_id, location=location)
        except Exception as e:
            logger.warning(f"Could not initialize ADK AgentRegistry client: {e}")
    return None

def get_gcp_auth_provider() -> Optional[object]:
    """Returns an ADK GcpAuthProvider instance if available."""
    if GcpAuthProvider is not None:
        try:
            return GcpAuthProvider()
        except Exception as e:
            logger.warning(f"Could not initialize ADK GcpAuthProvider: {e}")
    return None

def get_user_gcp_token(tool_context: Optional[Context]) -> str:
    """Attempts to retrieve the user's GCP/Google Drive OAuth token from ADK Context, then falls back to Application Default Credentials (ADC)."""
    if tool_context is not None:
        def extract_token_from_obj(cred) -> Optional[str]:
            if not cred:
                return None
            if isinstance(cred, str):
                return cred
            if isinstance(cred, dict):
                return cred.get("access_token") or cred.get("token")
            if hasattr(cred, "oauth2") and cred.oauth2 and getattr(cred.oauth2, "access_token", None):
                return cred.oauth2.access_token
            if hasattr(cred, "http") and cred.http and getattr(cred.http, "credentials", None):
                token = getattr(cred.http.credentials, "token", None)
                if token:
                    return token
            if hasattr(cred, "access_token") and getattr(cred, "access_token", None):
                return getattr(cred, "access_token")
            if hasattr(cred, "token") and getattr(cred, "token", None):
                return getattr(cred, "token")
            return None

        # 1. Check invocation_context.credential_by_key if available
        try:
            inv_context = getattr(tool_context, "_invocation_context", None)
            if inv_context and hasattr(inv_context, "credential_by_key"):
                for key, cred in inv_context.credential_by_key.items():
                    tok = extract_token_from_obj(cred)
                    if tok:
                        logger.info(f"Loaded access token from credential_by_key['{key}']")
                        return tok
        except Exception as e:
            logger.warning(f"Error checking credential_by_key: {e}")

        # 2. Check get_credential with common candidate keys
        candidate_keys = ["google_drive", "google-drive", "drive", "google", "gcp", "user_oauth", "default"]
        for key in candidate_keys:
            try:
                credential = tool_context.get_credential(key)
                tok = extract_token_from_obj(credential)
                if tok:
                    logger.info(f"Loaded access token from tool_context.get_credential('{key}')")
                    return tok
            except Exception as e:
                logger.debug(f"Could not load credential for key '{key}': {e}")

        # 3. Check session state dictionary
        try:
            state_dict = {}
            if hasattr(tool_context.state, "to_dict"):
                state_dict = tool_context.state.to_dict()
            elif tool_context.state:
                state_dict = dict(tool_context.state)

            for k, v in state_dict.items():
                if isinstance(k, str) and any(term in k.lower() for term in ["drive", "gcp", "google", "oauth", "token"]):
                    if isinstance(v, str) and v and (v.startswith("ya29.") or len(v) > 20):
                        logger.info(f"Loaded access token from session state key: {k}")
                        return v
                    elif isinstance(v, dict):
                        token = v.get("access_token") or v.get("token")
                        if isinstance(token, str) and token:
                            logger.info(f"Loaded access token from session state dict key: {k}")
                            return token
        except Exception as e:
            logger.warning(f"Could not read state dict: {e}")

    # 4. Fallback to Application Default Credentials (ADC)
    import google.auth
    import google.auth.transport.requests
    logger.info("Falling back to Application Default Credentials (ADC).")
    creds, project = google.auth.default(scopes=[
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/discoveryengine.readwrite",
        "https://www.googleapis.com/auth/discoveryengine",
        "https://www.googleapis.com/auth/cloud-platform"
    ])
    auth_req = google.auth.transport.requests.Request()
    creds.refresh(auth_req)
    return creds.token

def get_discovery_engine_location(tool_context: Optional[Context]) -> str:
    """Dynamically determines the location of the Gemini Enterprise / Discovery Engine instance.

    Inspection Order:
    1. ADK Invocation Context / session resource attributes (e.g. projects/.../locations/{loc}/...).
    2. Environment variables: DISCOVERY_ENGINE_LOCATION, GEMINI_ENTERPRISE_LOCATION, GOOGLE_CLOUD_LOCATION, GOOGLE_CLOUD_REGION, LOCATION.
    3. Multi-region normalization: Single-region GCP locations (e.g. us-central1) map to 'global' (or 'eu').
    """
    raw_loc = None
    if tool_context is not None:
        try:
            inv_context = getattr(tool_context, "_invocation_context", None)
            if inv_context:
                for attr_name in ["resource_name", "session_id", "app_id", "engine_id"]:
                    val = getattr(inv_context, attr_name, None)
                    if isinstance(val, str):
                        match = re.search(r"locations/([a-zA-Z0-9-_]+)", val)
                        if match:
                            raw_loc = match.group(1).lower()
                            logger.info(f"Discovered location candidate from ADK context ({attr_name}): {raw_loc}")
                            break
        except Exception as e:
            logger.debug(f"Could not inspect ADK context for location: {e}")

    if not raw_loc:
        for env_var in ["DISCOVERY_ENGINE_LOCATION", "GEMINI_ENTERPRISE_LOCATION", "GOOGLE_CLOUD_LOCATION", "GOOGLE_CLOUD_REGION", "LOCATION"]:
            val = os.environ.get(env_var)
            if val and val.strip():
                raw_loc = val.strip().lower()
                logger.info(f"Loaded location candidate from environment variable '{env_var}': {raw_loc}")
                break

    if not raw_loc:
        return "global"

    if raw_loc in ("us", "eu", "global"):
        return raw_loc

    if raw_loc.startswith("eu-") or raw_loc.startswith("europe-"):
        return "eu"
    if raw_loc.startswith("us-"):
        return os.environ.get("DISCOVERY_ENGINE_LOCATION", "global").lower()

    return "global"
