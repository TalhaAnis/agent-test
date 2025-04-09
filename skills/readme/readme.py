#!/usr/bin/env python3
import sys
import json
import requests
from urllib.parse import urlparse

GITHUB_API = "https://api.github.com"


def describe():
    print(json.dumps({
        "name": "readme",
        "description": "Builds a project tree and lists raw URLs from a GitHub repo to help LLM generate README.md.",
        "commands": [
            {
                "usage": "generate README for repo",
                "description": "Analyzes GitHub repo structure and builds a raw file list for LLM to generate README"
            }
        ],
        "context": {
            "source_repo_url": "GitHub repo URL (required)",
            "source_repo_branch": "Branch name (optional, default 'main')",
            "source_repo_username": "GitHub username (optional)",
            "source_repo_token": "GitHub token (optional for private repos)"
        }
    }, indent=2))


def get_system_prompt():
    prompt = (
        "You are a helpful assistant that can generate README.md files from GitHub repositories.\n\n"
        "Only respond with `use:readme generate README for <repo-url>` if the user explicitly asks you to generate a README or analyze a GitHub repo.\n"
        "For all other inputs, respond like a normal assistant and DO NOT call this skill.\n\n"
        "When the user asks to generate a README or something similar (e.g., generate doc) and you are confident that's what they want, respond with:\n"
        "  use:readme generate README for <repo-url>\n\n"
        "You will receive structured input (as a map) including repo structure and raw file URLs.\n"
        "Respond only with the final README content in markdown format. Be creative. Use headings, tables, and mermaid diagrams if helpful."
    )
    print(json.dumps({"system_prompt": prompt}))


def list_files(owner, repo, branch, token=None):
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    api_url = f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    r = requests.get(api_url, headers=headers)
    if r.status_code != 200:
        raise Exception(f"GitHub API error: {r.status_code} {r.text}")

    data = r.json()
    files = [
        {
            "path": item["path"],
            "raw_url": f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{item['path']}"
        }
        for item in data.get("tree", [])
        if item["type"] == "blob" and not any(part.startswith(".") for part in item["path"].split("/"))
    ]

    tree_text = "\n".join(["├── " + f["path"] for f in files])
    return files, tree_text


def run_generate(context):
    repo_url = context.get("source_repo_url")
    if not repo_url:
        return {"response": "❌ Missing 'source_repo_url' in context.", "retry": False, "error": True}

    branch = context.get("source_repo_branch", "main")
    token = context.get("source_repo_token")

    parsed = urlparse(repo_url)
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 2:
        return {"response": "❌ Invalid GitHub URL.", "retry": False, "error": True}

    owner, repo = parts[0], parts[1].replace(".git", "")

    try:
        files, tree = list_files(owner, repo, branch, token)
    except Exception as e:
        return {"response": f"❌ Failed to analyze repo: {e}", "retry": False, "error": True}

    return {
        "response": {
            "instruction": (
                "The following JSON contains details of a GitHub repository including the file tree and raw file URLs.\n\n"
                "Please generate a professional README.md using this data. Format the output in **markdown** and include the following sections if it is pertinent to the repository; you can use your expert judgement as well to make it one of the best README docs out there, like for example if some of the section doesnt make sense dont include it, some of the new section need to be added, feel free to add it:\n"
                "- Title\n- Description\n- Features\n- Tech Stack\n- Setup Instructions\n- Directory Structure\n- License\n\n"
                "Use the `project_structure`, `repo_url`, and `files` below to infer details. Be creative and extremely descriptive. Use headings, tables, and mermaid diagrams as it is necessary. Respond only with the final README.md content."
            ),
            "repo_url": repo_url,
            "branch": branch,
            "project_structure": tree,
            "files": files
        },
        "retry": False
    }


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--describe":
            describe()
            sys.exit(0)
        elif sys.argv[1] == "--prompt":
            get_system_prompt()
            sys.exit(0)

    if len(sys.argv) < 3:
        print(json.dumps({
            "response": "❓ Invalid arguments. Expected input and context.",
            "retry": True
        }))
        sys.exit(1)

    user_input = sys.argv[1]
    context = json.loads(sys.argv[2])

    if user_input.lower().startswith("generate readme"):
        result = run_generate(context)
    else:
        result = {
            "response": "❓ Unknown command. Try 'generate README for <repo-url>'.",
            "retry": True
        }

    print(json.dumps(result))
