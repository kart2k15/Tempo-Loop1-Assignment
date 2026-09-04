from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from app.metrics import ContributorStats
from app.narrative import NarrativeGenerationError, generate_narrative

STATS = [ContributorStats(login="alice", commits=5, prs_merged=2, lines_changed=100)]


def cli_envelope(result_text: str, is_error: bool = False) -> str:
    return json.dumps({"type": "result", "is_error": is_error, "result": result_text})


def mock_run(stdout: str = "", returncode: int = 0, side_effect=None):
    if side_effect is not None:
        return patch("app.narrative.subprocess.run", side_effect=side_effect)
    completed = subprocess.CompletedProcess(args=["claude"], returncode=returncode, stdout=stdout, stderr="")
    return patch("app.narrative.subprocess.run", return_value=completed)


class TestGenerateNarrative:
    def test_parses_well_formed_response(self):
        model_json = json.dumps(
            {
                "narrative": "Alice dominated activity.",
                "root_cause_hypothesis": "Alice is the maintainer.",
                "confidence": 0.8,
                "evidence": ["alice: 5 commits"],
            }
        )
        with mock_run(stdout=cli_envelope(model_json)):
            result = generate_narrative("o/r", "2025-01-01", "2025-02-01", STATS)

        assert result.narrative == "Alice dominated activity."
        assert result.root_cause_hypothesis == "Alice is the maintainer."
        assert result.confidence == 0.8
        assert result.evidence == ["alice: 5 commits"]

    def test_strips_markdown_code_fences(self):
        model_json = json.dumps(
            {"narrative": "n", "root_cause_hypothesis": None, "confidence": 0.5, "evidence": []}
        )
        fenced = f"```json\n{model_json}\n```"
        with mock_run(stdout=cli_envelope(fenced)):
            result = generate_narrative("o/r", "2025-01-01", "2025-02-01", STATS)

        assert result.narrative == "n"
        assert result.root_cause_hypothesis is None

    def test_missing_root_cause_hypothesis_key_defaults_to_none(self):
        model_json = json.dumps({"narrative": "n", "confidence": 0.5, "evidence": []})
        with mock_run(stdout=cli_envelope(model_json)):
            result = generate_narrative("o/r", "2025-01-01", "2025-02-01", STATS)
        assert result.root_cause_hypothesis is None

    def test_cli_is_error_raises(self):
        with mock_run(stdout=cli_envelope("something went wrong", is_error=True)):
            with pytest.raises(NarrativeGenerationError, match="reported an error"):
                generate_narrative("o/r", "2025-01-01", "2025-02-01", STATS)

    def test_nonzero_exit_code_raises(self):
        with mock_run(stdout="", returncode=1):
            with pytest.raises(NarrativeGenerationError, match="exited 1"):
                generate_narrative("o/r", "2025-01-01", "2025-02-01", STATS)

    def test_malformed_envelope_json_raises(self):
        with mock_run(stdout="not json at all"):
            with pytest.raises(NarrativeGenerationError, match="valid JSON envelope"):
                generate_narrative("o/r", "2025-01-01", "2025-02-01", STATS)

    def test_malformed_model_response_json_raises(self):
        with mock_run(stdout=cli_envelope("not json at all")):
            with pytest.raises(NarrativeGenerationError, match="not valid JSON"):
                generate_narrative("o/r", "2025-01-01", "2025-02-01", STATS)

    def test_missing_required_field_raises(self):
        model_json = json.dumps({"narrative": "n"})  # missing confidence, evidence
        with mock_run(stdout=cli_envelope(model_json)):
            with pytest.raises(NarrativeGenerationError, match="missing expected fields"):
                generate_narrative("o/r", "2025-01-01", "2025-02-01", STATS)

    def test_timeout_raises(self):
        with mock_run(side_effect=subprocess.TimeoutExpired(cmd=["claude"], timeout=60)):
            with pytest.raises(NarrativeGenerationError, match="timed out"):
                generate_narrative("o/r", "2025-01-01", "2025-02-01", STATS)

    def test_empty_stats_still_produces_a_prompt_that_does_not_crash(self):
        model_json = json.dumps({"narrative": "no activity", "confidence": 0.9, "evidence": []})
        with mock_run(stdout=cli_envelope(model_json)) as run_mock:
            generate_narrative("o/r", "2025-01-01", "2025-02-01", [])
        prompt_arg = run_mock.call_args.args[0][2]
        assert "No commits or merged PRs" in prompt_arg
