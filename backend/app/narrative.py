from __future__ import annotations

import json
import subprocess
from typing import List, Optional

from pydantic import BaseModel

from app.config import settings
from app.metrics import ContributorStats


class NarrativeResult(BaseModel):
    narrative: str
    root_cause_hypothesis: Optional[str] = None
    confidence: float
    evidence: List[str]


class NarrativeGenerationError(Exception):
    """Raised when the claude CLI fails, times out, or returns something we can't parse."""


def _build_prompt(repo: str, since: str, until: str, stats: List[ContributorStats]) -> str:
    if not stats:
        data_lines = "No commits or merged PRs were recorded for this repo in this period."
    else:
        data_lines = "\n".join(
            f"- {s.login}: {s.commits} commits, {s.prs_merged} merged PRs, {s.lines_changed} lines changed"
            for s in stats
        )
    return (
        f"You are analyzing GitHub contributor activity for {repo} between {since} and {until}.\n"
        f"Data (already computed - do not recompute or invent numbers not listed):\n{data_lines}\n\n"
        "Respond with ONLY a JSON object (no markdown code fences, no other text) with exactly these keys:\n"
        "- narrative: 2-4 sentences on what stands out in the data above\n"
        "- root_cause_hypothesis: a plausible reason for the pattern, or null if the signal doesn't support one\n"
        "- confidence: a number from 0 to 1\n"
        "- evidence: an array of short strings, each citing a specific number from the data above"
    )


def _strip_markdown_fences(text: str) -> str:
    """LLMs frequently wrap JSON in ```json ... ``` despite instructions not to."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines)
    return text


def generate_narrative(repo: str, since: str, until: str, stats: List[ContributorStats]) -> NarrativeResult:
    prompt = _build_prompt(repo, since, until, stats)

    try:
        proc = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=settings.claude_cli_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise NarrativeGenerationError(f"claude CLI timed out after {settings.claude_cli_timeout_seconds}s") from exc

    if proc.returncode != 0:
        raise NarrativeGenerationError(f"claude CLI exited {proc.returncode}: {proc.stderr.strip()[:500]}")

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise NarrativeGenerationError("claude CLI did not return a valid JSON envelope") from exc

    if envelope.get("is_error"):
        raise NarrativeGenerationError(f"claude CLI reported an error: {envelope.get('result')}")

    result_text = envelope.get("result", "")
    try:
        parsed = json.loads(_strip_markdown_fences(result_text))
    except json.JSONDecodeError as exc:
        raise NarrativeGenerationError(f"model response was not valid JSON: {result_text[:300]!r}") from exc

    try:
        return NarrativeResult(**parsed)
    except Exception as exc:
        raise NarrativeGenerationError(f"model response missing expected fields: {exc}") from exc
