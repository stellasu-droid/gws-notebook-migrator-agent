# NotebookLM Migration Agent for Gemini Enterprise

An **ADK (Google Agent Development Kit)** agent that enables users to migrate **NotebookLM** export archives (Google Takeout `.zip` files or Google Drive folders) into **Gemini Enterprise Notebooks**.

---

## 🛠️ End-to-End Setup & Deployment Guide

### Prerequisites (Local Machine)
Ensure you have the following installed and configured before starting:
- **Python 3.11+**
- **Gcloud CLI**
- **uv**

Authenticate your local environment to your Google Cloud Project:
```
gcloud auth application-default login
```

---

### Step 1: Create OAuth 2.0 App (Google Cloud Console)
1. Go to **Google Auth Platform** -> **Clients**.
2. Create an **OAuth 2.0 Client ID** (Web application).
3. In **Authorized redirect URIs**, add `https://vertexaisearch.cloud.google.com/oauth-redirect`.

### Step 2: Grant User IAM Roles (Google Cloud Console)
Make sure users have **Discovery Engine User** (`roles/discoveryengine.user`) to access Gemini Enterprise and perform notebook migrations:
```
gcloud projects add-iam-policy-binding YOUR_GCP_PROJECT_ID \
    --member="user:user_email@domain.com" \
    --role="roles/discoveryengine.user"
```

### Step 3: Deploy the Agent to Vertex AI Agent Runtime
Run the deployment script to build and deploy the agent to **Vertex AI Agent Runtime**:

```
./deploy.sh \
  --project-id YOUR_GCP_PROJECT_ID \
  --region us-central1 \
  --model gemini-2.5-flash
```

### Step 4: Publish Agent in Gemini Enterprise 
After the deployment completes successfully, publish the agent in Gemini Enterprise (Click **+ Add agent** -> Select **Custom agent via Agent Runtime**):

1. **Add Authorization**:
   - **Authorization Name**: `google_drive`
   - **Token URI**: `https://oauth2.googleapis.com/token`
   - **Authorization URI**:
     ```text
     https://accounts.google.com/o/oauth2/auth?access_type=offline&prompt=consent&response_type=code&scope=https://www.googleapis.com/auth/cloud-platform+https://www.googleapis.com/auth/drive.readonly+https://www.googleapis.com/auth/documents+https://www.googleapis.com/auth/presentations+https://www.googleapis.com/auth/discoveryengine.readwrite
     ```
   - **Client ID**: Your OAuth Client ID created in Step 1
   - **Client Secret**: Your OAuth Client Secret created in Step 1

---

## 📋 Overview & Migration Flow

### Purpose
Google Workspace NotebookLM users can export their notebooks via Google Takeout ZIP archive files. This agent automates scanning, parsing, and recreating those notebooks inside **Gemini Enterprise Notebooks** via standard REST APIs.

### End-to-End Migration Flow
1. **Drive Link**: The user provides a Google Drive link to their NotebookLM Takeout folder.
2. **Extraction & Scanning (`unzipper.py` + `parser.py`)**:
   - Downloads and unzips multi-part archives (`takeout-*-001.zip`, `takeout-*-1-001.zip`) and inner archives (`NotebookLM.zip`).
   - Scans and catalog all notebooks, sources (PDFs, HTML, YouTube links), written notes, and web research references.
3. **Interactive Selection**: Discovers all notebooks and prompts the user to select which notebooks to migrate (`1`, `1, 2`, or `'all'`).
4. **OAuth Token Inspection**: Resolves the user's 3-legged OAuth access token via ADK context (`_invocation_context`) so the agent can act on behalf of the user.
5. **Notebook & Source Creation (`migrator.py`)**:
   - Creates the target notebook resource under specified location.
   - Batch imports sources (text, PDF, HTML, YouTube).
   - Imports written notes and web research references as knowledge sources.
6. **Summary Report**: Renders a Markdown summary report showing migrated notebooks, source counts, and indexing notices.

---

## 📁 Repository Structure

```text
notebook_migrator_agent/
├── app/
│   ├── agent.py               # Main ADK Agent & summary report generator
│   ├── agent_runtime_app.py   # Vertex AI Agent Runtime entrypoint
│   ├── unzipper.py            # Multi-part ZIP downloader & extractor
│   ├── parser.py              # Takeout scanner & HTML text converter
│   ├── migrator.py            # Discovery Engine REST API client
│   ├── utils.py               # OAuth token & location helpers
│   └── schemas.py             # Pydantic v2 data models
├── deploy.sh                  # One-click deployment script
├── pyproject.toml             # Python package & deployment dependencies
├── .gitignore                 # Git ignore rules
└── README.md                  # Technical documentation
```

---

## ⚙️ Customization & Environment Variables

Technical users can customize deployment behavior using command-line arguments or environment variables:

| Parameter | CLI Flag | Environment Variable | Default Value | Description |
|---|---|---|---|---|
| **GCP Project ID** | `--project-id` | `GOOGLE_CLOUD_PROJECT` | *(Required)* | Target GCP project where the agent runtime and Gemini Enterprise reside. |
| **Agent Runtime Region** | `--region` | `GOOGLE_CLOUD_REGION` | `us-central1` | Region where the Vertex AI Agent Engine container is hosted. |
| **Gemini Model Version** | `--model` | `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini LLM model (`gemini-2.5-flash` or `gemini-2.5-pro`). |
| **Notebook Destination** | N/A | `DISCOVERY_ENGINE_LOCATION` | `global` | Location for Gemini Enterprise Notebook creation (`global`, `us`, `eu`). |