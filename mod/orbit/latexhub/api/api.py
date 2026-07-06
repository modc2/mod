"""
latexhub api - FastAPI server for LaTeX document management

Endpoints:
    GET  /health         - health check
    GET  /docs           - list all documents
    POST /docs           - save a document
    GET  /docs/{name}    - load a document
    DELETE /docs/{name}  - delete a document
    POST /docs/{name}/compile - compile to PDF
    GET  /docs/{name}/pdf     - download compiled PDF
    GET  /search         - search documents
    GET  /folders        - list folders
    GET  /status         - service status

Usage:
    uvicorn api.api:app --host 0.0.0.0 --port 50200 --reload
"""

import os
import sys
from typing import Optional, List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from latexhub.mod import Mod as LatexHub

app = FastAPI(title="LatexHub API", version="1.0.0",
              description="Local filesystem LaTeX document storage and management")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

hub = LatexHub()


# ── request models ───────────────────────────────────────────────────

class SaveRequest(BaseModel):
    name: str
    content: str
    folder: Optional[str] = None
    tags: Optional[List[str]] = None


class CompileRequest(BaseModel):
    engine: str = "pdflatex"


# ── routes ───────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "module": "latexhub", "version": "1.0.0"}


@app.get("/docs")
def list_docs(folder: Optional[str] = None, tags: Optional[str] = None):
    tag_list = tags.split(",") if tags else None
    docs = hub.ls(folder=folder, tags=tag_list)
    return {"docs": docs, "count": len(docs)}


@app.post("/docs")
def save_doc(req: SaveRequest):
    result = hub.save(name=req.name, content=req.content,
                      folder=req.folder, tags=req.tags)
    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result


@app.get("/docs/{name}")
def load_doc(name: str, folder: Optional[str] = None):
    result = hub.load(name=name, folder=folder)
    if 'error' in result:
        raise HTTPException(status_code=404, detail=result['error'])
    return result


@app.delete("/docs/{name}")
def delete_doc(name: str, folder: Optional[str] = None):
    result = hub.rm(name=name, folder=folder)
    if 'error' in result:
        raise HTTPException(status_code=404, detail=result['error'])
    return result


@app.post("/docs/{name}/compile")
def compile_doc(name: str, req: CompileRequest = CompileRequest(),
                folder: Optional[str] = None):
    result = hub.compile(name=name, folder=folder, engine=req.engine)
    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result


@app.get("/docs/{name}/pdf")
def get_pdf(name: str, folder: Optional[str] = None):
    doc_dir = hub._doc_dir(name, folder)
    pdf_path = os.path.join(doc_dir, 'out', 'main.pdf')
    if not os.path.exists(pdf_path):
        raise HTTPException(status_code=404, detail="PDF not found. Compile first.")
    return FileResponse(pdf_path, media_type="application/pdf",
                        filename=f"{name}.pdf")


@app.get("/search")
def search_docs(q: str, folder: Optional[str] = None):
    results = hub.search(query=q, folder=folder)
    return {"results": results, "count": len(results), "query": q}


@app.get("/folders")
def list_folders():
    folders = []
    if os.path.exists(hub.storage_root):
        for entry in os.listdir(hub.storage_root):
            path = os.path.join(hub.storage_root, entry)
            if os.path.isdir(path):
                # check if it's a folder (contains subdirs with main.tex) vs a doc
                tex = os.path.join(path, 'main.tex')
                if not os.path.exists(tex):
                    folders.append(entry)
    return {"folders": folders}


@app.get("/status")
def get_status():
    docs = hub.ls()
    return {
        "module": "latexhub",
        "storage": hub.storage_root,
        "doc_count": len(docs),
        "status": "ok",
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 50200))
    uvicorn.run("api.api:app", host="0.0.0.0", port=port, reload=True)
