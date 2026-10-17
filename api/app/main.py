import dataclasses

from fastapi import FastAPI

from app.clock import clock
from app.config import settings
from app.pathway import load_pathway
from app.routers import cases

pathway = load_pathway(settings.pathway_path)
cases.pathway = pathway

app = FastAPI(title="Screening Closure Engine", version="0.1.0")
app.include_router(cases.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "sim_time": clock.now().isoformat()}


@app.get("/pathways")
def pathways() -> dict:
    """Return the loaded pathway config as parsed from YAML."""
    return pathway.raw
