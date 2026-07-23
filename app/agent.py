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
import json
import tempfile
import logging
from typing import Optional, Any, Union, List
from google.genai.types import HttpRetryOptions
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.agents.readonly_context import ReadonlyContext

from .schemas import NotebookLMInstance
from .unzipper import process_source_location
from .parser import parse_takeout_directory
from .migrator import migrate_single_notebook
from .utils import get_user_gcp_token, get_discovery_engine_location

logger = logging.getLogger("notebook_migrator.agent")
logger.setLevel(logging.INFO)

SCRATCH_DIR = os.path.join(tempfile.gettempdir(), "notebook_migrator_agent_scratch")
PARSED_JSON_PATH = os.path.join(SCRATCH_DIR, "parsed_notebooks.json")

async def download_and_parse_takeout_tool(
    source_link_or_path: str,
    tool_context: ReadonlyContext
) -> str:
    """Downloads Google Takeout ZIP file(s) from Google Drive (or uses local directory/ZIP), handles multi-part ZIP extraction, and parses all NotebookLM instances inside.

    Args:
        source_link_or_path: Google Drive URL link to Takeout file/folder, or local folder path.
        tool_context: Injected ADK Context.

    Returns:
        A Markdown catalog summary table of discovered notebooks.
    """
    logger.info(f"download_and_parse_takeout_tool called with: {source_link_or_path}")
    os.makedirs(SCRATCH_DIR, exist_ok=True)

    oauth_token = None
    if "drive.google.com" in source_link_or_path or "docs.google.com" in source_link_or_path:
        try:
            oauth_token = get_user_gcp_token(tool_context)
        except Exception as e:
            logger.warning(f"Could not load OAuth token for GDrive: {e}")

    notebooks_root = process_source_location(source_link_or_path, SCRATCH_DIR, oauth_token)
    if not notebooks_root:
        return "Error: Could not access or locate 'NotebookLM' export directory in the provided Google Drive link. Please ensure the link is a valid Google Drive shareable link to your Takeout export file or folder, and that Google Drive access permissions are granted."

    notebooks = parse_takeout_directory(notebooks_root)
    if not notebooks:
        return f"Error: No valid NotebookLM notebook folders were found inside the provided export archive."

    try:
        serialized = [nb.model_dump() for nb in notebooks]
        with open(PARSED_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(serialized, f, indent=2)
    except Exception as e:
        return f"Error saving parsed notebook metadata: {e}"

    table_lines = [
        "### 📦 NotebookLM Takeout Export Analyzed Successfully!\n",
        "Here are the notebooks discovered in your export archive:\n",
        "| ID | Emoji | Notebook Title | Sources | Web Discoveries | Notes / Artifacts | Chat History |",
        "|---|---|---|---|---|---|---|",
    ]

    for nb in notebooks:
        src_summary = {}
        for s in nb.sources:
            t = s.content_type.replace("SOURCE_CONTENT_TYPE_", "")
            src_summary[t] = src_summary.get(t, 0) + 1
        src_str = ", ".join(f"{c} {t}" for t, c in src_summary.items()) if src_summary else "0 sources"

        disc_str = f"{len(nb.discovered_sources)} web results" if nb.discovered_sources else "None"
        art_str = f"{len(nb.artifacts)} items" if nb.artifacts else "0 items"
        chat_str = "Yes" if nb.has_chat_history else "No"

        table_lines.append(f"| {nb.id} | {nb.emoji} | {nb.title} | {src_str} | {disc_str} | {art_str} | {chat_str} |")

    table_markdown = "\n".join(table_lines)
    return (
        f"{table_markdown}\n\n"
        f"Please reply with the notebook number(s) you would like to migrate to Gemini Enterprise (for example: `1`, `1, 2`, or `all`).\n"
        f"*(Note: You must specify valid notebook numbers such as 1, 2, 3... or 'all'. Any unrecognized text or invalid options will not be processed.)*"
    )

async def migrate_selected_notebooks_tool(
    selected_ids: Any,
    tool_context: ReadonlyContext
) -> str:
    """Migrates selected NotebookLM instances into Gemini Enterprise.

    Args:
        selected_ids: Notebook number(s) or selection (e.g. 1, '1, 2', or 'all').
        tool_context: Injected ADK Context.

    Returns:
        A Markdown migration summary report.
    """
    if not os.path.exists(PARSED_JSON_PATH):
        return "Error: No parsed notebooks found. Please provide a Google Drive Takeout link first."

    try:
        with open(PARSED_JSON_PATH, "r", encoding="utf-8") as f:
            data_list = json.load(f)
    except Exception as e:
        return f"Error reading parsed notebook metadata: {e}"

    if not data_list:
        return "Error: No discovered notebooks available to migrate."

    # Robustly convert selected_ids (handles int, str, list, etc.)
    if isinstance(selected_ids, (list, tuple)):
        raw_str = ",".join(str(x) for x in selected_ids)
    else:
        raw_str = str(selected_ids)

    clean_ids = raw_str.strip().strip("'\"`").strip().lower()

    selected_indices = []
    if clean_ids in ("all", "everything", "all notebooks", "select all") or "all" in clean_ids.split():
        selected_indices = list(range(len(data_list)))
    else:
        # Extract all distinct positive numbers from user input
        extracted_numbers = re.findall(r'\b\d+\b', clean_ids)
        for num_str in extracted_numbers:
            idx = int(num_str) - 1
            if 0 <= idx < len(data_list) and idx not in selected_indices:
                selected_indices.append(idx)

    if not selected_indices:
        valid_range = f"1 to {len(data_list)}" if len(data_list) > 1 else "1"
        return (
            f"Error: Unrecognized selection '{selected_ids}'.\n"
            f"Please select valid notebook number(s) from the inventory table above (valid options: {valid_range}, or 'all').\n"
            f"Note: Inputs other than the listed notebook numbers (e.g. 1, 2, 3) or 'all' cannot be recognized."
        )

    try:
        token = get_user_gcp_token(tool_context)
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project_id:
            return "Error: GOOGLE_CLOUD_PROJECT environment variable is not set."
        location = get_discovery_engine_location(tool_context)

        results = []
        for idx in selected_indices:
            instance_dict = data_list[idx]
            instance = NotebookLMInstance.model_validate(instance_dict)
            res = migrate_single_notebook(instance, token, project_id, location=location, apis_allowlisted=True)
            results.append(res)

        report = [
            "# 🚀 NotebookLM Migration Summary Report\n",
            "> [!NOTE]",
            "> ⏳ **Note**: Newly migrated notebooks may take a few minutes to appear in your Gemini Enterprise workspace. If your new notebook does not appear immediately, please refresh your browser after a short wait.\n"
        ]

        for r in results:
            if not r.get("success"):
                report.append(f"## ❌ {r.get('notebook_title')}\n- **Error**: {r.get('error')}\n")
                continue

            report.append(f"## ✅ {r.get('notebook_title')}")
            report.append(f"- **Gemini Enterprise Notebook ID**: `{r.get('notebook_id')}`")

            created_sources = r.get("created_sources", [])
            failed_sources = r.get("failed_sources", [])
            report.append(f"- **Sources Migrated Successfully** ({len(created_sources)}):")
            for s in created_sources:
                report.append(f"  - `{s['title']}` (ID: `{s['id']}`)")

            if failed_sources:
                report.append(f"- **Sources Failed** ({len(failed_sources)}):")
                for s in failed_sources:
                    report.append(f"  - ⚠️ `{s['title']}`: *{s['error']}*")

            created_artifacts = r.get("created_artifacts", [])
            skipped_artifacts = r.get("skipped_artifacts", [])
            if created_artifacts:
                report.append(f"- **Created Notes & Artifacts** ({len(created_artifacts)}):")
                for a in created_artifacts:
                    report.append(f"  - `{a['title']}` (Type: `{a['type']}`, ID: `{a['id']}`)")

            if skipped_artifacts:
                report.append(f"- **Non-Imported Items** ({len(skipped_artifacts)}):")
                for a in skipped_artifacts:
                    report.append(f"  - `{a['title']}`: *{a['reason']}*")

            report.append("")

        return "\n".join(report)
    except Exception as main_ex:
        logger.error(f"Unhandled exception during notebook migration: {main_ex}")
        return f"Error during migration execution: {main_ex}. Please try again."

# ===========================================================================
# Define Root ADK Agent & App
# ===========================================================================

model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

gemini_model = Gemini(
    model=model_name,
    retry_options=HttpRetryOptions(
        attempts=5,
        initial_delay=2.0,
        max_delay=30.0,
        http_status_codes=[429, 500, 502, 503, 504],
    ),
)

root_agent = Agent(
    name="notebooklm_migrator",
    model=gemini_model,
    description="Assistant for migrating NotebookLM Takeout exports into Gemini Enterprise Notebooks.",
    instruction="""You are a helpful assistant that migrates NotebookLM exports into Gemini Enterprise.

Follow this simple, user-friendly workflow:
1. Greet the user warmly and ask them to share the Google Drive link to their NotebookLM Takeout ZIP file or folder.
2. When the user provides the Google Drive link, invoke `download_and_parse_takeout_tool` passing the URL in `source_link_or_path`.
3. Present the resulting Markdown inventory table showing all discovered notebooks.
4. Ask the user: "Which notebook numbers would you like to migrate? (for example: 1, 2, or 'all')?" Explain clearly that only listed notebook numbers (e.g. 1, 2, 3) or 'all' will be recognized.
5. When the user responds (whether they provide a number like 1, a list like 1, 2, or 'all'), invoke `migrate_selected_notebooks_tool` passing their selection in `selected_ids`.
6. If `migrate_selected_notebooks_tool` returns an error about an unrecognized selection, inform the user clearly and prompt them to enter valid notebook numbers from the table.
7. Present the final Markdown migration summary report upon successful migration.

Do not ask technical questions about APIs or backend parameters. Keep all interactions friendly and focused on helping the user complete their migration.
""",
    tools=[download_and_parse_takeout_tool, migrate_selected_notebooks_tool],
)

app = App(
    root_agent=root_agent,
    name="app",
)
