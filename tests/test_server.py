from glm_worker_mcp.server import _format_result


def test_format_result_full():
    result = {
        "final_message": "Done.",
        "files_changed": [
            {"path": "a.txt", "action": "written", "count": 10},
            {"path": "b.py", "action": "edited", "count": 2},
        ],
        "assumptions": ["nested keys"],
        "couldnt_do": ["c.js: encoding"],
        "status": "ok",
        "metrics": {"model": "glm-5.2", "turns_used": 5, "tool_calls": 9,
                    "tokens": {"prompt": 100, "completion": 20, "total": 120},
                    "duration_seconds": 3.1},
    }
    out = _format_result(result)
    assert "written  a.txt  (10 lines)" in out
    assert "edited   b.py  (2 edit(s))" in out
    assert "nested keys" in out and "c.js: encoding" in out
    assert "model=glm-5.2" in out and "tokens=120" in out
