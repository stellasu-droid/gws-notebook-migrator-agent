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
import io
import json
import time
import zipfile
import shutil
import logging
import urllib.request
import urllib.parse
from typing import Optional, List, Tuple

logger = logging.getLogger("notebook_migrator.unzipper")
logger.setLevel(logging.INFO)

def extract_drive_id(url: str) -> Tuple[Optional[str], str]:
    """Extracts Google Drive file ID or folder ID from a URL.
    Returns (id, type) where type is 'file' or 'folder' or 'unknown'.
    """
    folder_match = re.search(r'/folders/([a-zA-Z0-9-_]+)', url)
    if folder_match:
        return folder_match.group(1), "folder"

    file_match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    if file_match:
        return file_match.group(1), "file"

    file_id_param = re.search(r'[?&]id=([a-zA-Z0-9-_]+)', url)
    if file_id_param:
        return file_id_param.group(1), "unknown"

    return None, "unknown"

def download_drive_file_by_id(file_id: str, oauth_token: Optional[str] = None) -> Optional[bytes]:
    """Downloads a single file from Google Drive using an OAuth token or fallback public URL."""
    headers = {}
    if oauth_token:
        headers["Authorization"] = f"Bearer {oauth_token}"
        get_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
        logger.info(f"Downloading file from GDrive using OAuth token: {file_id}")
        req = urllib.request.Request(get_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            logger.warning(f"OAuth download HTTP error {e.code} for file {file_id}: {e.reason}")
        except Exception as e:
            logger.warning(f"OAuth download failed for file {file_id}: {e}. Trying public download fallback...")

    # Fallback attempt for public/anybody-with-link shared files
    public_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    logger.info(f"Attempting public fallback download: {public_url}")
    req_pub = urllib.request.Request(public_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req_pub, timeout=120) as response:
            content = response.read()
            # Handle large file confirmation page
            if content and b"confirm=" in content:
                match = re.search(r'confirm=([0-9a-zA-Z_]+)', content.decode('utf-8', errors='ignore'))
                if match:
                    confirm_code = match.group(1)
                    confirm_url = f"https://drive.google.com/uc?export=download&confirm={confirm_code}&id={file_id}"
                    req_confirm = urllib.request.Request(confirm_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req_confirm, timeout=120) as resp2:
                        content = resp2.read()
            if content and len(content) > 100 and not content.strip().startswith(b"<!DOCTYPE html"):
                return content
    except Exception as ex:
        logger.error(f"Public download fallback failed for file {file_id}: {ex}")

    return None

def download_drive_folder(folder_id: str, target_dir: str, oauth_token: Optional[str] = None) -> bool:
    """Recursively lists and downloads all files inside a Google Drive folder."""
    headers = {}
    if oauth_token:
        headers["Authorization"] = f"Bearer {oauth_token}"

    files_list = []
    page_token = None

    while True:
        list_url = f"https://www.googleapis.com/drive/v3/files?q='{folder_id}'+in+parents+and+trashed=false&fields=nextPageToken,files(id,name,mimeType)"
        if page_token:
            list_url += f"&pageToken={urllib.parse.quote(page_token)}"

        logger.info(f"Listing files in GDrive folder: {folder_id}")
        req = urllib.request.Request(list_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))
                files_list.extend(data.get("files", []))
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            if e.code == 403 and ("SCOPE" in err_body.upper() or "PERMISSION" in err_body.upper()):
                logger.error(f"Google Drive API 403 Forbidden: Insufficient OAuth scope for folder {folder_id}. Request requires 'https://www.googleapis.com/auth/drive.readonly' scope. Detail: {err_body}")
            else:
                logger.error(f"Failed to list files in Google Drive folder {folder_id}: HTTP Error {e.code} ({e.reason}). Detail: {err_body}")
            return False
        except Exception as e:
            logger.error(f"Failed to list files in Google Drive folder {folder_id}: {e}")
            return False

    if not files_list:
        logger.warning(f"No files found in Google Drive folder {folder_id}.")
        return False

    downloaded_any = False
    for file_info in files_list:
        f_id = file_info.get("id")
        f_name = file_info.get("name", "file")
        f_mime = file_info.get("mimeType", "")

        if f_mime == "application/vnd.google-apps.folder":
            sub_dir = os.path.join(target_dir, f_name)
            os.makedirs(sub_dir, exist_ok=True)
            if download_drive_folder(f_id, sub_dir, oauth_token):
                downloaded_any = True
        else:
            logger.info(f"Downloading file from folder: {f_name} (ID: {f_id})")
            content = download_drive_file_by_id(f_id, oauth_token)
            if content:
                out_path = os.path.join(target_dir, f_name)
                with open(out_path, "wb") as f_out:
                    f_out.write(content)
                downloaded_any = True
                logger.info(f"Saved file {f_name} to {out_path}")

    return downloaded_any

def extract_zip_stream(zip_bytes: bytes, target_dir: str) -> bool:
    """Extracts ZIP bytes into target_dir."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            z.extractall(target_dir)
        logger.info(f"Successfully extracted ZIP bytes to {target_dir}")
        return True
    except Exception as e:
        logger.error(f"Failed to extract ZIP bytes: {e}")
        return False

def extract_all_zips_in_directory(target_dir: str) -> None:
    """Recursively checks target_dir for any .zip files and extracts them into target_dir."""
    for _ in range(3):
        found_zip = False
        for root, dirs, files in os.walk(target_dir):
            for f in files:
                if f.lower().endswith('.zip'):
                    zip_path = os.path.join(root, f)
                    logger.info(f"Extracting nested/multi-part archive: {zip_path}")
                    try:
                        with zipfile.ZipFile(zip_path, 'r') as z:
                            z.extractall(target_dir)
                        os.remove(zip_path)
                        found_zip = True
                    except Exception as e:
                        logger.warning(f"Could not extract zip {zip_path}: {e}")
        if not found_zip:
            break

def find_notebooks_root(extracted_dir: str) -> Optional[str]:
    """Recursively searches for the 'NotebookLM' directory containing notebook subfolders."""
    for root, dirs, files in os.walk(extracted_dir):
        if os.path.basename(root) == "NotebookLM":
            subdirs = [d for d in dirs if d not in ("Chat History", "Artifacts", "Sources", "Notes", "Discovered Sources")]
            if subdirs:
                return root

    for entry in os.listdir(extracted_dir):
        sub_path = os.path.join(extracted_dir, entry)
        if os.path.isdir(sub_path):
            for f in os.listdir(sub_path):
                if f.endswith(".json") and os.path.isfile(os.path.join(sub_path, f)):
                    return extracted_dir

    return None

def process_source_location(source_link_or_path: str, scratch_dir: str, oauth_token: Optional[str] = None) -> Optional[str]:
    """Processes input path or GDrive link, extracts all multi-part ZIPs, and returns the resolved NotebookLM root folder."""
    target_extract_dir = os.path.join(scratch_dir, "extracted_takeout")
    if os.path.exists(target_extract_dir):
        shutil.rmtree(target_extract_dir)
    os.makedirs(target_extract_dir, exist_ok=True)

    is_drive = "drive.google.com" in source_link_or_path or "docs.google.com" in source_link_or_path or "drive.usercontent.google.com" in source_link_or_path

    if is_drive:
        file_id, drive_type = extract_drive_id(source_link_or_path)
        if not file_id:
            logger.error(f"Could not parse Google Drive file/folder ID from: {source_link_or_path}")
            return None

        # Try to query item metadata if OAuth token is available to verify mimeType
        if oauth_token and drive_type == "unknown":
            try:
                headers = {"Authorization": f"Bearer {oauth_token}"}
                meta_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?fields=id,name,mimeType"
                req = urllib.request.Request(meta_url, headers=headers)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    meta = json.loads(resp.read().decode("utf-8"))
                    if meta.get("mimeType") == "application/vnd.google-apps.folder":
                        drive_type = "folder"
                    else:
                        drive_type = "file"
            except Exception as e:
                logger.warning(f"Could not fetch Drive metadata for item {file_id}: {e}")

        if drive_type == "folder":
            success = download_drive_folder(file_id, target_extract_dir, oauth_token)
            if not success:
                logger.error(f"Failed to download Google Drive folder {file_id}.")
                return None
        else:
            zip_bytes = download_drive_file_by_id(file_id, oauth_token)
            if not zip_bytes:
                logger.error(f"Failed to download file bytes from Google Drive for file ID {file_id}.")
                return None

            success = extract_zip_stream(zip_bytes, target_extract_dir)
            if not success:
                # If extraction fails directly, save file raw to target_extract_dir in case it's a zip or archive file
                out_file = os.path.join(target_extract_dir, "downloaded_takeout.zip")
                with open(out_file, "wb") as f_out:
                    f_out.write(zip_bytes)

    else:
        local_path = os.path.abspath(source_link_or_path)
        if not os.path.exists(local_path):
            logger.error(f"Local path does not exist: {local_path}")
            return None

        if os.path.isfile(local_path) and local_path.lower().endswith('.zip'):
            shutil.copy(local_path, os.path.join(target_extract_dir, "source.zip"))
        elif os.path.isdir(local_path):
            for item in os.listdir(local_path):
                src_item = os.path.join(local_path, item)
                dst_item = os.path.join(target_extract_dir, item)
                if os.path.isdir(src_item):
                    shutil.copytree(src_item, dst_item)
                else:
                    shutil.copy2(src_item, dst_item)

    extract_all_zips_in_directory(target_extract_dir)

    notebooks_root = find_notebooks_root(target_extract_dir)
    if notebooks_root:
        logger.info(f"Resolved NotebookLM root folder: {notebooks_root}")
        return notebooks_root

    logger.error("Could not locate NotebookLM root folder in extracted Takeout contents.")
    return None
