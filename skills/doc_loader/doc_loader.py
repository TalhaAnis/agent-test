#!/usr/bin/env python3
import sys
import json
import traceback
import os
import shutil
import importlib.util
import subprocess
import warnings
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor
import requests
from bs4 import BeautifulSoup

warnings.simplefilter("ignore", DeprecationWarning)
os.environ["PYTHONWARNINGS"] = "ignore"
sys.stderr = open(os.devnull, 'w')

REQUIRED_BINARIES = ["curl", "python3", "git"]
REQUIRED_PYTHON_PACKAGES = [
    "langchain", "openai", "chromadb", "bs4", "sentence_transformers",
    "langchain_community", "requests"
]

PERSIST_DIR = "/tmp/doc_loader_vector_db"
SCRAPE_ALREADY_RAN = False

def check_binary_dependency(binary):
    try:
        subprocess.run([binary, "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        print(json.dumps({
            "response": f"Missing required binary: {binary}",
            "error": True,
            "retry": False
        }))
        sys.exit(1)

def check_python_package(package):
    if importlib.util.find_spec(package) is None:
        print(json.dumps({
            "response": f"Missing required Python package: {package}",
            "error": True,
            "retry": False
        }))
        sys.exit(1)

def run_dependency_checks():
    for binary in REQUIRED_BINARIES:
        check_binary_dependency(binary)
    for package in REQUIRED_PYTHON_PACKAGES:
        check_python_package(package)

def is_git_repo_url(url):
    return "repo1.dso.mil" in url or url.endswith(".git")

def clone_and_load_md(url):
    temp_dir = "/tmp/doc_loader_git"
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    try:
        subprocess.run(["git", "clone", url, temp_dir], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return []
    md_docs = []
    from langchain_community.document_loaders import TextLoader
    for root, _, files in os.walk(temp_dir):
        for file in files:
            if file.endswith(".md"):
                try:
                    loader = TextLoader(os.path.join(root, file), encoding="utf-8", errors="ignore")
                    docs = loader.load()
                    md_docs.extend([d for d in docs if d.page_content and len(d.page_content.strip()) > 50])
                except Exception:
                    try:
                        with open(os.path.join(root, file), encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                            if len(content.strip()) > 50:
                                from langchain_core.documents import Document
                                md_docs.append(Document(page_content=content))
                    except Exception:
                        continue
    shutil.rmtree(temp_dir)
    return md_docs

def scrape_url_recursive(url, visited=None, depth=0, max_depth=3):
    if visited is None:
        visited = set()
    if url in visited or depth > max_depth:
        return []
    visited.add(url)
    docs = []
    try:
        from langchain_community.document_loaders import WebBaseLoader
        loader = WebBaseLoader(url)
        raw_docs = loader.load()
        docs.extend([d for d in raw_docs if d.page_content and len(d.page_content.strip()) > 50])
    except Exception:
        pass
    try:
        response = requests.get(url, timeout=5)
        soup = BeautifulSoup(response.text, "html.parser")
        for a in soup.find_all("a", href=True):
            link = a["href"]
            if link.startswith("/"):
                link = urljoin(url, link)
            if url.split("://")[1].split("/")[0] in link:
                docs.extend(scrape_url_recursive(link, visited, depth + 1, max_depth))
    except Exception:
        pass
    return docs

def handle_prompt_flag():
    print(json.dumps({
        "system_prompt": (
            "You are a skill named 'doc_loader' that scrapes websites and Git repos (markdown docs) and builds a vector DB for question answering.\n\n"
            "- When the user says anything like 'scrape the docs', 'scrape site', or 'load documentation', reply exactly with:\n"
            "use:doc_loader scrape and load docs\n\n"
            "- When the user asks a question, reply exactly with:\n"
            "use:doc_loader ask: <their question>"
        )
    }))
    sys.exit(0)

def handle_describe_flag():
    print(json.dumps({
        "name": "doc_loader",
        "description": "Scrapes URLs or clones Git repos (.md files) and builds a vector database for LLM-powered answers.",
        "commands": [
            {
                "usage": "use:doc_loader scrape and load docs",
                "description": "Scrape all target URLs or Git repos in agent.yaml and index them."
            },
            {
                "usage": "use:doc_loader ask: <your question>",
                "description": "Ask questions based on indexed content (RAG)."
            }
        ],
        "context": {
            "target_urls": "List of URLs or Git repos to scrape",
            "vector_db_url": "Optional",
            "vector_db_auth": "Optional"
        }
    }))
    sys.exit(0)

def main():
    global SCRAPE_ALREADY_RAN
    if "--prompt" in sys.argv:
        handle_prompt_flag()
    if "--describe" in sys.argv:
        handle_describe_flag()

    run_dependency_checks()

    try:
        user_input = sys.argv[1].strip() if len(sys.argv) > 1 else "index"
        context = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

        if user_input.lower().startswith("scrape") or user_input.lower().startswith("load"):
            user_input = "index"

        from langchain.text_splitter import RecursiveCharacterTextSplitter
        from langchain.embeddings import HuggingFaceEmbeddings
        from langchain_community.vectorstores import Chroma

        embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

        if user_input.lower().startswith("ask:"):
            question = user_input.split("ask:", 1)[1].strip()
            if not os.path.exists(PERSIST_DIR):
                print(json.dumps({
                    "response": question,
                    "context": {
                        "retrieved_chunks": 0,
                        "index_found": False
                    }
                }))
                return

            db = Chroma(persist_directory=PERSIST_DIR, embedding_function=embeddings)
            retriever = db.as_retriever(search_kwargs={"k": 5})
            docs = retriever.get_relevant_documents(question)

            summaries = []
            for doc in docs:
                text = doc.page_content.strip()
                if text:
                    summaries.append(text[:200] + ("..." if len(text) > 200 else ""))
            summary_text = "\n\n".join(summaries).strip()

            if not summary_text:
                print(json.dumps({
                    "response": question,
                    "context": {
                        "retrieved_chunks": 0,
                        "index_found": True
                    }
                }))
                return

            final_prompt = (
                f"Here is some potentially helpful information retrieved from documentation:\n\n{summary_text}\n\n"
                f"You are the expert in this context. Using this information as context, and combining it with your general knowledge, please provide a clear, thorough, and complete answer to the following question:\n\n"
                f"\"{question}\"\n\n"
                "Write a helpful, complete answer — even if the retrieved information is incomplete. You MUST Use your own knowledge as needed. "
                "If you're ABSOLUTELY uncertain, just say: 'This information is not available.'"
            )

            print(json.dumps({
                "response": {
                    "instruction": final_prompt
                },
                "context": {
                    "retrieved_chunks": len(docs),
                    "index_found": True
                }
            }))
            return

        if user_input == "index":
            if SCRAPE_ALREADY_RAN:
                print(json.dumps({
                    "response": "⚠️ Skipping re-scrape: already scraped during this session.",
                    "context": {
                        "already_scraped": True,
                        "session_cached": True
                    }
                }))
                return

            raw_urls = context.get("target_urls", "")
            urls = [url.strip() for url in raw_urls.split(",") if url.strip()]
            if not urls:
                raise ValueError("No valid URLs provided in 'target_urls'.")

            if os.path.exists(PERSIST_DIR):
                shutil.rmtree(PERSIST_DIR)

            splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=100)

            def smart_scrape(url):
                if is_git_repo_url(url):
                    return splitter.split_documents(clone_and_load_md(url))
                return splitter.split_documents(scrape_url_recursive(url, max_depth=3))

            all_chunks = []
            with ThreadPoolExecutor(max_workers=5) as executor:
                results = executor.map(smart_scrape, urls)
                for chunks in results:
                    all_chunks.extend(chunks)

            if not all_chunks:
                raise RuntimeError("No content could be scraped from the provided URLs.")

            db = Chroma.from_documents(all_chunks, embedding=embeddings, persist_directory=PERSIST_DIR)
            db.persist()
            SCRAPE_ALREADY_RAN = True

            print(json.dumps({
                "response": f"✅ Indexed {len(all_chunks)} chunks from {len(urls)} URLs.",
                "context": {
                    "num_urls": len(urls),
                    "num_chunks": len(all_chunks)
                }
            }))
            return

        raise ValueError(f"Unknown command: '{user_input}'")

    except Exception as e:
        print(json.dumps({
            "response": f"❌ Skill failed: {str(e)}",
            "error": True,
            "retry": False,
            "traceback": traceback.format_exc()
        }))
        sys.exit(1)

if __name__ == "__main__" or sys.argv[0] == "-":
    main()
