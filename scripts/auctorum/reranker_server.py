"""Auctorum reranker HTTP service.

Wraps `flashrank` (cross-encoder, ~21 MB ONNX) behind a FastAPI server
on port 8002. The Alienware-side `kee/distributed/reranker.py` consumes
this when `KEE_RERANKER_URL` points here.

Deploy on Auctorum:

    sudo apt install python3.12 python3-venv
    sudo mkdir -p /opt/reranker && sudo chown $USER /opt/reranker
    python3.12 -m venv /opt/reranker/venv
    source /opt/reranker/venv/bin/activate
    pip install fastapi uvicorn[standard] flashrank pydantic

Save this file to /opt/reranker/server.py, then create
/etc/systemd/system/reranker.service:

    [Unit]
    Description=Kee reranker (flashrank cross-encoder)
    After=network.target

    [Service]
    Type=simple
    ExecStart=/opt/reranker/venv/bin/python /opt/reranker/server.py
    Restart=always
    User=<your-user>
    Environment=PYTHONUNBUFFERED=1

    [Install]
    WantedBy=multi-user.target

Then:
    sudo systemctl daemon-reload
    sudo systemctl enable --now reranker
    sudo ufw allow from 100.64.0.0/10 to any port 8002

Smoke test from Alienware:
    curl -X POST http://auctorum:8002/rerank \
      -H 'Content-Type: application/json' \
      -d '{"query":"qué hace AUCTORUM",
           "documents":["AUCTORUM provides WhatsApp AI agents",
                        "Snake game in pygame"]}'
"""

from __future__ import annotations

import logging
import os
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uvicorn

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("reranker")


# Lazy-import flashrank so the server fails late with a clear message if
# the model isn't installed.
_RANKER = None
_MODEL_NAME = os.environ.get("RERANKER_MODEL", "ms-marco-MiniLM-L-12-v2")


def _get_ranker():
    global _RANKER
    if _RANKER is None:
        log.info("Loading flashrank model %r (cold start ~3s)…", _MODEL_NAME)
        t0 = time.time()
        from flashrank import Ranker
        _RANKER = Ranker(model_name=_MODEL_NAME, max_length=512)
        log.info("Loaded in %.2fs.", time.time() - t0)
    return _RANKER


app = FastAPI(title="Kee reranker", version="1.0.0")


class RerankRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    documents: list[str] = Field(..., min_length=1, max_length=200)


class RerankItem(BaseModel):
    document: str
    score: float
    index: int


class RerankResponse(BaseModel):
    query: str
    model: str
    elapsed_ms: int
    results: list[RerankItem]


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "model": _MODEL_NAME,
        "loaded": _RANKER is not None,
    }


@app.post("/rerank", response_model=RerankResponse)
def rerank(req: RerankRequest) -> RerankResponse:
    if not req.documents:
        raise HTTPException(status_code=400, detail="documents empty")
    t0 = time.time()
    ranker = _get_ranker()
    # flashrank wants {"id": int, "text": str} entries
    payload = [{"id": i, "text": d} for i, d in enumerate(req.documents)]
    from flashrank import RerankRequest as FRRequest
    fr_req = FRRequest(query=req.query, passages=payload)
    scored = ranker.rerank(fr_req)
    # scored is list of dicts {id, text, score}; sort desc by score
    scored.sort(key=lambda r: r["score"], reverse=True)
    elapsed_ms = int((time.time() - t0) * 1000)
    return RerankResponse(
        query=req.query,
        model=_MODEL_NAME,
        elapsed_ms=elapsed_ms,
        results=[
            RerankItem(document=r["text"],
                       score=float(r["score"]),
                       index=int(r["id"]))
            for r in scored
        ],
    )


if __name__ == "__main__":
    port = int(os.environ.get("RERANKER_PORT", "8002"))
    log.info("Starting reranker server on 0.0.0.0:%d", port)
    # Pre-warm the model so the first request isn't a 3 s cold start.
    _get_ranker()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
