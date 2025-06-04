#!/usr/bin/env python3
import os
import subprocess
import requests
import json
import sys

# ─── 1. Load environment variables ────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN")  # Provided automatically by Actions
GITHUB_REF     = os.getenv("GITHUB_REF")    # e.g. "refs/heads/main"

if not GEMINI_API_KEY:
    print("🚨 Error: GEMINI_API_KEY is not set.", file=sys.stderr)
    sys.exit(1)

# Extract branch name from GITHUB_REF
# e.g. "refs/heads/main" → "main"
branch = GITHUB_REF.split("/")[-1]

# ─── 2. Identify changed .md files between HEAD^ and HEAD ───────────────────────────
# Ensure we have full git history
subprocess.run(["git", "fetch", "--no-tags", "--prune", "--unshallow"], check=False)

diff_files_proc = subprocess.run(
    ["git", "diff", "--name-only", "HEAD^", "HEAD"],
    capture_output=True,
    text=True,
    check=True
)
changed_files = [
    f.strip()
    for f in diff_files_proc.stdout.splitlines()
    if f.strip().lower().endswith(".md")
]

if not changed_files:
    print("ℹ️  No Markdown files changed in this commit. Exiting.")
    sys.exit(0)

# ─── 3. For each changed .md file, fetch its diff and full content, send to Gemini ───
for md_path in changed_files:
    # 3a. Read full file content
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            original_content = f.read()
    except FileNotFoundError:
        # The file might have been deleted—skip it
        continue

    # 3b. Get the diff for that file only
    diff_proc = subprocess.run(
        ["git", "diff", "HEAD^", "HEAD", "--", md_path],
        capture_output=True,
        text=True,
        check=True
    )
    diff_text = diff_proc.stdout

    # 3c. Build the Gemini prompt
    prompt = f"""
You are an AI assistant whose job is to reformat Markdown into GitBook-style. 
**Only reformat sections that have changed** according to the diff. 
If the diff indicates that many parts of the file have changed, 
the AI may decide to reformat the entire file. 
Do NOT add explanations—return only the updated full file content 
(treat the input as one document).

--- DIFF (between HEAD^ and HEAD) ---
{diff_text}
--- END DIFF ---

--- FULL ORIGINAL CONTENT ---
{original_content}
--- END ORIGINAL CONTENT ---
"""

    gemini_payload = {
        "contents": [
            { "parts": [ { "text": prompt } ] }
        ]
    }

    # 3d. Call Gemini-2 API
    gemini_resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}",
        headers={ "Content-Type": "application/json" },
        data=json.dumps(gemini_payload)
    )

    if gemini_resp.status_code != 200:
        print(f"⚠️  Gemini API returned {gemini_resp.status_code} for {md_path}:", file=sys.stderr)
        print(gemini_resp.text, file=sys.stderr)
        continue

    new_content = gemini_resp.json()["candidates"][0]["content"]["parts"][0]["text"]

    # 3e. Overwrite the file if content changed
    if new_content.strip() and new_content != original_content:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        subprocess.run(["git", "add", md_path], check=True)
        print(f"✅  Reformatted and staged {md_path}")
    else:
        print(f"ℹ️  No changes needed for {md_path}")

# ─── 4. Commit & Push if anything was changed ──────────────────────────────────────
# Configure a Git user for committing
subprocess.run(["git", "config", "user.name", "github-actions"], check=True)
subprocess.run(
    ["git", "config", "user.email", "github-actions@users.noreply.github.com"],
    check=True
)

# Attempt to commit; if there are no staged changes, this will return non-zero, so ignore errors
commit_proc = subprocess.run(
    ["git", "commit", "-m", "chore(docs): reformat Markdown via Gemini AI"],
    check=False
)

if commit_proc.returncode == 0:
    # 4a. Push back to the same branch
    push_proc = subprocess.run(
        ["git", "push", "origin", branch],
        capture_output=True,
        text=True,
        check=False
    )
    if push_proc.returncode == 0:
        print("📤  Successfully pushed reformatted files.")
    else:
        print("❌  Failed to push changes:", file=sys.stderr)
        print(push_proc.stderr, file=sys.stderr)
        sys.exit(1)
else:
    print("ℹ️  No reformat commits to push.")
