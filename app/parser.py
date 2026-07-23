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
import re
import html
import html.parser
import logging
from typing import List, Optional, Tuple, Dict, Any
from .schemas import NotebookLMInstance, SourceItem, ArtifactItem, DiscoveredSourceItem

logger = logging.getLogger("notebook_migrator.parser")
logger.setLevel(logging.INFO)

class HTMLTextExtractor(html.parser.HTMLParser):
    """HTML parser that converts HTML document structure into formatted plain text/markdown."""
    def __init__(self):
        super().__init__()
        self.lines: List[str] = []
        self.current_line: List[str] = []
        self.in_script_or_style: bool = False

    def _flush_line(self):
        line_str = "".join(self.current_line).strip()
        if line_str:
            self.lines.append(line_str)
        self.current_line = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]):
        tag = tag.lower()
        if tag in ("script", "style"):
            self.in_script_or_style = True
        elif tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br", "li"):
            self._flush_line()
            if tag == "h1":
                self.current_line.append("# ")
            elif tag == "h2":
                self.current_line.append("## ")
            elif tag == "h3":
                self.current_line.append("### ")
            elif tag == "li":
                self.current_line.append("- ")

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in ("script", "style"):
            self.in_script_or_style = False
        elif tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "li"):
            self._flush_line()

    def handle_data(self, data: str):
        if not self.in_script_or_style:
            self.current_line.append(data)

    def get_text(self) -> str:
        self._flush_line()
        raw_text = "\n".join(self.lines)
        return html.unescape(raw_text).strip()

def extract_text_from_html(html_content: str) -> str:
    """Parses HTML content and returns structured plain text."""
    parser = HTMLTextExtractor()
    try:
        parser.feed(html_content)
        parser.close()
        text = parser.get_text()
        return text if text else re.sub(r'<[^>]+>', '', html_content).strip()
    except Exception as e:
        logger.warning(f"HTMLTextExtractor error fallback to regex: {e}")
        return html.unescape(re.sub(r'<[^>]+>', '', html_content)).strip()

def parse_notebook_directory(notebook_dir: str, idx: int) -> NotebookLMInstance:
    """Parses a single notebook directory inside NotebookLM export."""
    folder_name = os.path.basename(notebook_dir)
    notebook_title = folder_name
    notebook_emoji = "📓"

    # 1. Parse Notebook Metadata JSON
    metadata_file = None
    for f in os.listdir(notebook_dir):
        if f.endswith(".json") and os.path.isfile(os.path.join(notebook_dir, f)):
            metadata_file = os.path.join(notebook_dir, f)
            break

    if metadata_file:
        try:
            with open(metadata_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
                notebook_title = meta.get("title", notebook_title)
                notebook_emoji = meta.get("emoji", notebook_emoji)
        except Exception as e:
            logger.warning(f"Error reading notebook metadata {metadata_file}: {e}")

    # 2. Parse Sources
    sources: List[SourceItem] = []
    sources_dir = os.path.join(notebook_dir, "Sources")
    if os.path.exists(sources_dir) and os.path.isdir(sources_dir):
        files_in_sources = os.listdir(sources_dir)
        meta_jsons = [f for f in files_in_sources if f.endswith(".json") or f.endswith("metadata.json")]
        
        for meta_f in sorted(meta_jsons):
            meta_path = os.path.join(sources_dir, meta_f)
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    src_meta = json.load(f)
                    src_title = src_meta.get("title", "")
                    metadata_obj = src_meta.get("metadata", {})
                    content_type = metadata_obj.get("originalSourceContentType", "SOURCE_CONTENT_TYPE_URL")

                    youtube_id = None
                    if content_type == "SOURCE_CONTENT_TYPE_YOUTUBE_VIDEO":
                        youtube_meta = metadata_obj.get("youtubeMetadata", {})
                        youtube_id = youtube_meta.get("videoId")

                    # Find corresponding HTML content file
                    base_name = meta_f
                    for suffix in (" metadata.json", ".json", "metadata.json"):
                        if base_name.endswith(suffix):
                            base_name = base_name[:-len(suffix)]
                            break

                    html_candidates = [
                        f"{base_name}.html",
                        f"{base_name}.pdf.html",
                        f"{base_name}.docx.html",
                        f"{base_name}.txt",
                        base_name
                    ]

                    content_file = None
                    for cand in html_candidates:
                        p = os.path.join(sources_dir, cand)
                        if os.path.exists(p) and os.path.isfile(p):
                            content_file = p
                            break

                    if not content_file:
                        # Prefix clean fallback
                        prefix_clean = base_name.lower().replace("_", "").replace("-", "")[:20]
                        for f_in_dir in files_in_sources:
                            if f_in_dir != meta_f and not f_in_dir.endswith(".json"):
                                f_clean = f_in_dir.lower().replace("_", "").replace("-", "")
                                if prefix_clean in f_clean:
                                    content_file = os.path.join(sources_dir, f_in_dir)
                                    break

                    sources.append(SourceItem(
                        title=src_title or base_name,
                        content_type=content_type,
                        file_path=content_file,
                        youtube_id=youtube_id
                    ))
            except Exception as e:
                logger.warning(f"Error parsing source metadata {meta_path}: {e}")

    # 3. Parse Discovered Sources
    discovered_sources: List[DiscoveredSourceItem] = []
    disc_overview: Optional[str] = None
    disc_query: Optional[str] = None

    disc_dir = os.path.join(notebook_dir, "Discovered Sources")
    if os.path.exists(disc_dir) and os.path.isdir(disc_dir):
        for disc_f in os.listdir(disc_dir):
            if disc_f.endswith(".json"):
                disc_path = os.path.join(disc_dir, disc_f)
                try:
                    with open(disc_path, "r", encoding="utf-8") as f:
                        disc_data = json.load(f)
                        job = disc_data.get("discoverSourcesJob", {})
                        disc_query = job.get("query")
                        results = job.get("discoverSourcesResults", {})
                        disc_overview = results.get("overview")
                        for item in results.get("discoveredSources", []):
                            discovered_sources.append(DiscoveredSourceItem(
                                source_url=item.get("sourceUrl", ""),
                                title=item.get("title", ""),
                                is_imported=item.get("isImported", False),
                                explanation=item.get("explanation")
                            ))
                except Exception as e:
                    logger.warning(f"Error reading discovered sources {disc_path}: {e}")

    # 4. Parse Artifacts & Notes
    artifacts: List[ArtifactItem] = []
    artifacts_dir = os.path.join(notebook_dir, "Artifacts")
    if os.path.exists(artifacts_dir) and os.path.isdir(artifacts_dir):
        files_in_art = os.listdir(artifacts_dir)
        meta_jsons = [f for f in files_in_art if f.endswith(".json") or f.endswith("metadata.json")]
        for meta_f in sorted(meta_jsons):
            meta_path = os.path.join(artifacts_dir, meta_f)
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    art_meta = json.load(f)
                    art_title = art_meta.get("title", "")
                    art_type = art_meta.get("type", "ARTIFACT_TYPE_UNKNOWN")
                    artifacts.append(ArtifactItem(
                        title=art_title or meta_f,
                        artifact_type=art_type,
                        file_path=meta_path,
                        metadata=art_meta
                    ))
            except Exception as e:
                logger.warning(f"Error reading artifact {meta_path}: {e}")

    notes_dir = os.path.join(notebook_dir, "Notes")
    if os.path.exists(notes_dir) and os.path.isdir(notes_dir):
        for note_f in os.listdir(notes_dir):
            note_path = os.path.join(notes_dir, note_f)
            if os.path.isfile(note_path):
                art_type = "ARTIFACT_TYPE_MIND_MAP" if "mindmap" in note_f.lower() else "ARTIFACT_TYPE_NOTE"
                artifacts.append(ArtifactItem(
                    title=os.path.splitext(note_f)[0],
                    artifact_type=art_type,
                    file_path=note_path
                ))

    # 5. Check Chat History
    chat_dir = os.path.join(notebook_dir, "Chat History")
    has_chat = os.path.exists(chat_dir) and os.path.isdir(chat_dir) and len(os.listdir(chat_dir)) > 0

    return NotebookLMInstance(
        id=idx,
        title=notebook_title,
        emoji=notebook_emoji,
        folder_name=folder_name,
        sources=sources,
        artifacts=artifacts,
        discovered_sources=discovered_sources,
        discovered_overview=disc_overview,
        discovered_query=disc_query,
        has_chat_history=has_chat
    )

def parse_takeout_directory(notebooks_root: str) -> List[NotebookLMInstance]:
    """Parses all notebook subdirectories in notebooks_root."""
    notebook_instances = []
    idx = 1

    entries = sorted(os.listdir(notebooks_root))
    for entry in entries:
        entry_path = os.path.join(notebooks_root, entry)
        if not os.path.isdir(entry_path):
            continue

        if entry in (".DS_Store", "__pycache__"):
            continue

        # Check if entry is a notebook folder (contains JSON or subfolders like Sources/Notes/Artifacts)
        has_metadata = any(f.endswith(".json") for f in os.listdir(entry_path))
        has_subdirs = any(os.path.isdir(os.path.join(entry_path, sub)) for sub in ("Sources", "Notes", "Artifacts", "Discovered Sources"))

        if has_metadata or has_subdirs:
            logger.info(f"Parsing notebook directory #{idx}: {entry}")
            instance = parse_notebook_directory(entry_path, idx)
            notebook_instances.append(instance)
            idx += 1

    return notebook_instances
