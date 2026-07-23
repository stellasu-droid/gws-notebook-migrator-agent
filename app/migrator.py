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
import json
import logging
import urllib.request
import urllib.parse
import time
from datetime import datetime
from typing import List, Dict, Any, Tuple
from .schemas import NotebookLMInstance, SourceItem, ArtifactItem
from .parser import extract_text_from_html

logger = logging.getLogger("notebook_migrator.migrator")
logger.setLevel(logging.INFO)

def make_api_call(url: str, method: str, headers: Dict[str, str], body: Any = None, max_retries: int = 4) -> Tuple[int, Dict[str, Any], str]:
    """Helper function to perform HTTP calls to Discovery Engine API with exponential backoff for 429/5xx errors."""
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

    for attempt in range(max_retries):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                resp_body = response.read().decode("utf-8")
                status = response.status
                try:
                    resp_json = json.loads(resp_body)
                except Exception:
                    resp_json = {}
                return status, resp_json, resp_body
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            logger.warning(f"HTTP Error {e.code} on {method} {url} (attempt {attempt+1}/{max_retries}): {err_body}")
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                sleep_time = (2 ** attempt) + 0.5
                logger.info(f"Rate limited or transient server error ({e.code}). Retrying in {sleep_time:.1f}s...")
                time.sleep(sleep_time)
                continue
            try:
                err_json = json.loads(err_body)
            except Exception:
                err_json = {}
            return e.code, err_json, err_body
        except Exception as e:
            logger.error(f"Request failed to {method} {url} (attempt {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return 500, {}, str(e)

def map_artifact_to_note_prompt_type(art_type: str) -> str:
    if art_type == "ARTIFACT_TYPE_TAILORED_REPORT":
        return "BRIEFING_DOC"
    elif art_type in ("ARTIFACT_TYPE_APP", "ARTIFACT_TYPE_STUDY_GUIDE"):
        return "STUDY_GUIDE"
    elif art_type == "ARTIFACT_TYPE_FAQ":
        return "FAQ"
    elif art_type == "ARTIFACT_TYPE_TIMELINE":
        return "TIMELINE"
    elif art_type == "ARTIFACT_TYPE_MIND_MAP":
        return "MIND_MAP"
    else:
        return "NOTE_PROMPT_TYPE_UNSPECIFIED"

def get_token_diagnostics(token: str, project_id: str = "") -> str:
    """Inspects an OAuth access token to check email identity and granted scopes."""
    if not token or len(token) < 10:
        return "Token is missing or invalid."
    try:
        req = urllib.request.Request(f"https://oauth2.googleapis.com/tokeninfo?access_token={token}")
        info = {}
        with urllib.request.urlopen(req, timeout=10) as resp:
            info = json.loads(resp.read().decode("utf-8"))

        email = info.get("email")
        if not email:
            # Fallback to userinfo endpoint
            try:
                u_req = urllib.request.Request("https://www.googleapis.com/oauth2/v2/userinfo", headers={"Authorization": f"Bearer {token}"})
                with urllib.request.urlopen(u_req, timeout=10) as u_resp:
                    u_info = json.loads(u_resp.read().decode("utf-8"))
                    email = u_info.get("email")
            except Exception:
                email = None

        display_email = email if email else "unknown (email/openid scope omitted from Auth URI)"
        scopes = info.get("scope", "")
        scope_list = scopes.split()
        has_cloud_platform = any(s in scope_list for s in [
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/discoveryengine",
            "https://www.googleapis.com/auth/discoveryengine.readwrite"
        ])
        diag = [f"- **Authenticated User Email**: `{display_email}`"]
        diag.append(f"- **Token Scopes Granted**: `{scopes}`")

        if not has_cloud_platform:
            diag.append(
                "\n⚠️ **CRITICAL FINDING: Restricted OAuth Scope Stripped!**\n"
                "The access token issued to this user ONLY contains Google Drive scopes and lacks `cloud-platform` / `discoveryengine` scopes.\n"
                "Google Workspace Admin stripped the `cloud-platform` scope during sign-in because your OAuth Client ID is untrusted for regular users.\n"
                "**Fix**: Go to Google Workspace Admin Console (`admin.google.com -> Security -> API Controls -> App Access Control`), find your OAuth Client ID, and set its Access Status to **Trusted**."
            )
        else:
            member_binding = f"user:{email}" if email else "user:USER_EMAIL"
            diag.append(
                f"\n⚠️ **ROOT CAUSE FINDING: Role Permission Defect!**\n"
                f"Google Cloud's `Discovery Engine Admin` (`roles/discoveryengine.admin`) role DOES NOT contain `discoveryengine.notebooks.create`!\n"
                f"However, **`Discovery Engine User` (`roles/discoveryengine.user`)** DOES contain `discoveryengine.notebooks.create`!\n\n"
                f"**Fix**: Grant `roles/discoveryengine.user` to the user's email identity:\n"
                f"`gcloud projects add-iam-policy-binding {project_id} --member=\"{member_binding}\" --role=\"roles/discoveryengine.user\"`"
            )
        return "\n".join(diag)
    except Exception as e:
        return f"Could not inspect token info: {e}"

def migrate_single_notebook(
    instance: NotebookLMInstance,
    token: str,
    project_id: str,
    location: str = "global",
    apis_allowlisted: bool = True
) -> Dict[str, Any]:
    """Migrates a single NotebookLM instance into Gemini Enterprise."""
    logger.info(f"Starting migration for notebook: {instance.title}")

    headers = {
        "Authorization": f"Bearer {token}"
    }

    if location == "global":
        host = "discoveryengine.googleapis.com"
    elif location == "us":
        host = "us-discoveryengine.googleapis.com"
    else:
        host = f"{location}-discoveryengine.googleapis.com"

    create_url = f"https://{host}/v1alpha/projects/{project_id}/locations/{location}/notebooks"

    time_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    display_title = f"{instance.emoji} {instance.title} (Migrated {time_str})"
    create_payload = {
        "title": display_title
    }

    status, resp_json, resp_raw = make_api_call(create_url, "POST", headers, create_payload)
    if status not in (200, 201):
        logger.error(f"Failed to create notebook: status={status}, response={resp_raw}")
        err_detail = "Migration failed due to an authorization or backend service error. Please ask your Google Cloud Administrator to check permissions and system logs."

        return {
            "success": False,
            "error": err_detail,
            "notebook_title": display_title
        }

    notebook_id = resp_json.get("notebookId")
    notebook_name = resp_json.get("name")
    logger.info(f"Successfully created notebook. ID: {notebook_id}, Resource Name: {notebook_name}")

    # Batch Add Sources
    created_sources_info = []
    failed_sources_info = []
    batch_size = 5

    for i in range(0, len(instance.sources), batch_size):
        chunk = instance.sources[i:i+batch_size]
        user_contents = []

        for src in chunk:
            user_content = None
            if src.content_type == "SOURCE_CONTENT_TYPE_YOUTUBE_VIDEO" and src.youtube_id:
                youtube_url = f"https://www.youtube.com/watch?v={src.youtube_id}"
                user_content = {
                    "videoContent": {
                        "youtubeUrl": youtube_url
                    }
                }
            elif src.file_path and os.path.exists(src.file_path):
                try:
                    with open(src.file_path, "r", encoding="utf-8") as f:
                        raw_content = f.read()
                    text_content = extract_text_from_html(raw_content)
                    user_content = {
                        "textContent": {
                            "sourceName": src.title,
                            "content": text_content
                        }
                    }
                except Exception as ex:
                    logger.warning(f"Error reading source file {src.file_path}: {ex}")
                    failed_sources_info.append({
                        "title": src.title,
                        "error": f"Failed to read file: {ex}"
                    })
            else:
                failed_sources_info.append({
                    "title": src.title,
                    "error": "Source file missing or unsupported content type"
                })

            if user_content:
                user_contents.append(user_content)

        if not user_contents:
            continue

        batch_url = f"https://{host}/v1alpha/{notebook_name}/sources:batchCreate"
        batch_payload = {"userContents": user_contents}

        b_status, b_resp_json, b_resp_raw = make_api_call(batch_url, "POST", headers, batch_payload)
        if b_status in (200, 201):
            sources_list = b_resp_json.get("sources", [])
            for s in sources_list:
                s_id = s.get("sourceId", {}).get("id")
                s_title = s.get("title", "")
                created_sources_info.append({"id": s_id, "title": s_title})
                logger.info(f"Created source: {s_title} (ID: {s_id})")
        else:
            logger.error(f"Failed to batch create sources: status={b_status}, response={b_resp_raw}")
            for src in chunk:
                failed_sources_info.append({"title": src.title, "error": f"API error status={b_status}"})

    # Create Discovered Sources Reference Note (if present)
    created_artifacts_info = []
    skipped_artifacts_info = []

    if instance.discovered_sources:
        disc_note_lines = ["# Discovered Web Sources\n"]
        if instance.discovered_query:
            disc_note_lines.append(f"**Original Search Query**: `{instance.discovered_query}`\n")
        if instance.discovered_overview:
            disc_note_lines.append(f"### Research Overview\n{instance.discovered_overview}\n")

        disc_note_lines.append("### Web Sources Found\n")
        for ds in instance.discovered_sources:
            imported_tag = " (Imported into Notebook)" if ds.is_imported else ""
            disc_note_lines.append(f"- **[{ds.title}]({ds.source_url})**{imported_tag}")
            if ds.explanation:
                disc_note_lines.append(f"  *Explanation*: {ds.explanation}")

        disc_note_content = "\n".join(disc_note_lines)
        batch_url = f"https://{host}/v1alpha/{notebook_name}/sources:batchCreate"
        batch_payload = {
            "userContents": [
                {
                    "textContent": {
                        "sourceName": "Note: Discovered Web Sources Reference",
                        "content": disc_note_content
                    }
                }
            ]
        }
        b_status, b_resp_json, _ = make_api_call(batch_url, "POST", headers, batch_payload)
        if b_status in (200, 201):
            sources_list = b_resp_json.get("sources", [])
            for s in sources_list:
                s_id = s.get("sourceId", {}).get("id", "note_src")
                created_sources_info.append({"id": s_id, "title": "Note: Discovered Web Sources Reference"})

    # Import Notes & Text Artifacts as Knowledge Sources
    for art in instance.artifacts:
        if art.file_path and os.path.isfile(art.file_path):
            content = None
            try:
                with open(art.file_path, "r", encoding="utf-8") as f:
                    raw = f.read()
                content = extract_text_from_html(raw)
            except Exception as ex:
                content = None

            if content:
                batch_url = f"https://{host}/v1alpha/{notebook_name}/sources:batchCreate"
                batch_payload = {
                    "userContents": [
                        {
                            "textContent": {
                                "sourceName": f"Note: {art.title}",
                                "content": content
                            }
                        }
                    ]
                }
                b_status, b_resp_json, _ = make_api_call(batch_url, "POST", headers, batch_payload)
                if b_status in (200, 201):
                    sources_list = b_resp_json.get("sources", [])
                    for s in sources_list:
                        s_id = s.get("sourceId", {}).get("id", "note_src")
                        created_sources_info.append({"id": s_id, "title": f"Note: {art.title}"})
                    continue

        skipped_artifacts_info.append({"title": art.title, "type": art.artifact_type, "reason": "Audio/Video overview or non-text format (supported only in-app)"})

    if instance.has_chat_history:
        skipped_artifacts_info.append({"title": "Chat History", "type": "CHAT_HISTORY", "reason": "Chat history is stateful and not supported by Notebooks API"})

    return {
        "success": True,
        "notebook_title": display_title,
        "notebook_id": notebook_id,
        "created_sources": created_sources_info,
        "failed_sources": failed_sources_info,
        "created_artifacts": created_artifacts_info,
        "skipped_artifacts": skipped_artifacts_info
    }
