from __future__ import annotations

from datetime import date
from typing import List

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
