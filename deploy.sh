#!/usr/bin/env bash
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

set -e

PROJECT_ID=""
REGION="us-central1"
MODEL="gemini-2.5-flash"

usage() {
  echo "Usage: $0 --project-id <GCP_PROJECT_ID> [--region <REGION>] [--model <MODEL_NAME>]"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-id)
      PROJECT_ID="$2"
      shift 2
      ;;
    --region)
      REGION="$2"
      shift 2
      ;;
    --model)
      MODEL="$2"
      shift 2
      ;;
    *)
      usage
      ;;
  esac
done

if [[ -z "$PROJECT_ID" ]]; then
  echo "Error: --project-id is required."
  usage
fi

echo "============================================================"
echo " Deploying NotebookLM Migration Agent to Vertex AI Agent Runtime"
echo " Project: ${PROJECT_ID}"
echo " Region:  ${REGION}"
echo " Model:   ${MODEL}"
echo "============================================================"

# Set active GCP project and region
gcloud config set project "${PROJECT_ID}" 2>/dev/null || true
export GOOGLE_CLOUD_PROJECT="${PROJECT_ID}"
export GOOGLE_CLOUD_REGION="${REGION}"
export GEMINI_MODEL="${MODEL}"
export GOOGLE_API_USE_MTLS="never"
export GOOGLE_API_USE_CLIENT_CERTIFICATE="false"
export UV_INDEX_URL="https://pypi.org/simple"

# Check for uv package manager
if ! command -v uv &> /dev/null; then
    echo "--> Installing 'uv' package manager..."
    pip install uv || curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# Step 1: Sync dependencies with uv
echo "--> Syncing dependencies with uv..."
uv sync

# Step 2: Deploy to Vertex AI Agent Runtime (Reasoning Engine)
echo "--> Deploying Agent to Vertex AI Agent Runtime..."
uv run agents-cli deploy --project "${PROJECT_ID}" --region "${REGION}" --update-env-vars "GEMINI_MODEL=${MODEL}"

echo "============================================================"
echo " ✅ Deployment to Vertex AI Agent Runtime completed!"
echo "============================================================"
