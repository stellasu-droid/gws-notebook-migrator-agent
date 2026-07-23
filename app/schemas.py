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

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class SourceItem(BaseModel):
    title: str = Field(description="Title of the source.")
    content_type: str = Field(description="Content type (e.g. SOURCE_CONTENT_TYPE_URL, SOURCE_CONTENT_TYPE_PDF, SOURCE_CONTENT_TYPE_DRIVE, SOURCE_CONTENT_TYPE_YOUTUBE_VIDEO).")
    file_path: Optional[str] = Field(None, description="Local path to HTML, TXT, or content file.")
    youtube_id: Optional[str] = Field(None, description="YouTube Video ID if applicable.")

class ArtifactItem(BaseModel):
    title: str = Field(description="Title of the artifact.")
    artifact_type: str = Field(description="Type of artifact (e.g. ARTIFACT_TYPE_AUDIO_OVERVIEW, ARTIFACT_TYPE_NOTE, ARTIFACT_TYPE_MIND_MAP).")
    file_path: Optional[str] = Field(None, description="Local path to artifact content file.")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata from takeout.")

class DiscoveredSourceItem(BaseModel):
    source_url: str = Field(description="URL of the discovered web source.")
    title: str = Field(description="Title of the discovered source.")
    is_imported: bool = Field(default=False, description="Whether the source was imported into NotebookLM.")
    explanation: Optional[str] = Field(None, description="Explanation or relevance snippet.")

class NotebookLMInstance(BaseModel):
    id: int = Field(description="1-based identifier for the notebook.")
    title: str = Field(description="Title of the notebook.")
    emoji: str = Field(default="📓", description="Emoji icon for the notebook.")
    folder_name: str = Field(description="Folder name inside Takeout.")
    sources: List[SourceItem] = Field(default_factory=list, description="List of sources.")
    artifacts: List[ArtifactItem] = Field(default_factory=list, description="List of artifacts and notes.")
    discovered_sources: List[DiscoveredSourceItem] = Field(default_factory=list, description="List of discovered web sources.")
    discovered_overview: Optional[str] = Field(None, description="Overview text from discovery job.")
    discovered_query: Optional[str] = Field(None, description="Original web search query.")
    has_chat_history: bool = Field(default=False, description="True if chat history exists.")

class SelectedNotebooksInput(BaseModel):
    selected_ids: str = Field(description="Comma-separated notebook IDs (e.g. '1, 2' or 'all').")
