from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel


class ContributorOut(BaseModel):
    login: str
    commits: int
    prs_merged: int
    lines_changed: int


class ContributorsResponse(BaseModel):
    repo: str
    since: date
    until: date
    contributors: List[ContributorOut]


class NarrativeResponse(BaseModel):
    repo: str
    since: date
    until: date
    narrative: str
    root_cause_hypothesis: Optional[str]
    confidence: float
    evidence: List[str]


class CollaborationEdgeOut(BaseModel):
    author: str
    reviewer: str
    reviews: int


class CollaborationResponse(BaseModel):
    repo: str
    since: date
    until: date
    edges: List[CollaborationEdgeOut]
