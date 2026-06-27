import ast
import os
from pathlib import Path
import pytest
from loop.spec import TaskSpec, ToolSchema
from loop.generator import (
    GenerationResult,
    generate_candidate,
    stub_generate,
    _render_agent,
)

_TOOLS = [
    ToolSchema(name="es_search", description="Search logs", input_schema={"type": "object", "properties": {}}),
    ToolSchema(name="field_stats", description="Field stats", input_schema={"type": "object", "properties": {}}),
]

_SPEC = TaskSpec(
    task_description="Analyse error rate",
    mcp_server_url="http://localhost:8000",
    available_tools=_TOOLS,
    eval_case_ids=["search_error_level"],
    max_iterations=5,
)


def test_stub_generate_returns_tuple():
    prompt, source = stub_generate(_SPEC, feedback="")
    assert isinstance(prompt, str) and len(prompt) > 0
    assert isinstance(source, str) and len(source) > 0


def test_stub_generate_produces_valid_python():
    _, source = stub_generate(_SPEC, feedback="")
    ast.parse(source)  # raises SyntaxError if invalid


def test_stub_generate_contains_tool_names():
    _, source = stub_generate(_SPEC, feedback="")
    assert "es_search" in source
    assert "field_stats" in source


def test_stub_generate_includes_feedback_comment():
    _, source = stub_generate(_SPEC, feedback="missing field foo")
    assert "missing field foo" in source


def test_stub_generate_has_main_entrypoint():
    _, source = stub_generate(_SPEC, feedback="")
    assert 'if __name__ == "__main__"' in source
    assert "asyncio.run(main())" in source


def test_stub_generate_prints_json_results():
    _, source = stub_generate(_SPEC, feedback="")
    assert "json.dumps(state" in source


def test_generate_candidate_writes_file(tmp_path):
    result = generate_candidate(_SPEC, iteration=0, generated_base_dir=tmp_path)
    assert isinstance(result, GenerationResult)
    assert result.path.exists()
    assert result.path.name == "agent.py"
    assert result.iteration == 0


def test_generate_candidate_path_contains_iteration(tmp_path):
    result = generate_candidate(_SPEC, iteration=2, generated_base_dir=tmp_path)
    assert "iteration_2" in str(result.path)
    assert "orch-b2a-agent" in str(result.path)


def test_generate_candidate_file_is_valid_python(tmp_path):
    result = generate_candidate(_SPEC, iteration=0, generated_base_dir=tmp_path)
    ast.parse(result.path.read_text())


def test_generate_candidate_raises_on_syntax_error(tmp_path):
    def bad_generator(spec, feedback):
        return "fake prompt", "def broken(:\n    pass"

    with pytest.raises(SyntaxError, match="ast.parse"):
        generate_candidate(_SPEC, iteration=0, generator_fn=bad_generator, generated_base_dir=tmp_path)


def test_generate_candidate_records_prompt(tmp_path):
    result = generate_candidate(_SPEC, iteration=0, generated_base_dir=tmp_path)
    assert len(result.prompt_sent) > 0


def test_generate_candidate_idempotent_on_same_spec(tmp_path):
    r1 = generate_candidate(_SPEC, iteration=0, generated_base_dir=tmp_path)
    r2 = generate_candidate(_SPEC, iteration=0, generated_base_dir=tmp_path)
    assert r1.path.read_text() == r2.path.read_text()
