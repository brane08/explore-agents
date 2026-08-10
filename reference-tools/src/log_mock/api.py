from fastapi import FastAPI
from pydantic import BaseModel, Field
from log_mock.generator import generate_logs

app = FastAPI(title="Mock Log API")

# Pre-generate a fixed corpus so filters are stable
_CORPUS = generate_logs(count=500, seed=0)


class _MatchQuery(BaseModel):
    match: dict[str, str] = Field(default_factory=dict)


class _SearchBody(BaseModel):
    size: int = 10
    query: _MatchQuery = Field(default_factory=_MatchQuery)


@app.post("/logs/_search")
def search_logs(body: _SearchBody) -> dict:
    results = _CORPUS
    if body.query.match:
        for field, value in body.query.match.items():
            results = [r for r in results if str(r.get(field, "")) == value]
    total = len(results)
    hits = results[: body.size]
    return {
        "hits": {
            "hits": [{"_source": r} for r in hits],
            "total": {"value": total},
        }
    }
