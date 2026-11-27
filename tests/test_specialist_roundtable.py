import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from botteam_kernel.server import create_app

ROLE_IDS = ["fundamentals", "news", "short_term", "long_term"]
PACKET = {
    "symbol": "DEMO-001",
    "as_of": "2026-11-20",
    "roles": ROLE_IDS,
    "request_id": "round-test-1",
    "actor": "研究员",
    "evidence": [
        {
            "id": "F1",
            "kind": "financial",
            "date": "2026-11-15",
            "source": "样例年报",
            "text": "现金流上升，资本开支也增长。",
        },
        {
            "id": "N1",
            "kind": "news",
            "date": "2026-11-21",
            "source": "样例公告",
            "text": "截止日后材料",
        },
    ],
}


def setup(api):
    g = api.post("/groups", json={"name": "研究群"}).json()["group_id"]
    root = f"/groups/{g}"
    assert api.post(
        root + "/members", json={"kind": "human", "name": "研究员"}
    ).is_success
    return root


def join(api, root, roles=ROLE_IDS):
    return api.post(root + "/specialists", json={"actor": "研究员", "roles": roles})


class Runner:
    mode = "test"

    def __init__(self, bad_role=None):
        self.calls = []
        self.bad_role = bad_role

    def __call__(self, *, role, prompt, evidence):
        self.calls.append((role, prompt, [e["id"] for e in evidence]))
        if role == self.bad_role:
            if role == "news":
                raise TimeoutError
            return {
                "observation": "坏引用",
                "judgment": "不应展示",
                "challenge": "—",
                "missing": "—",
                "citations": ["N1"],
            }
        return {
            "observation": "只读固定材料",
            "judgment": f"独立意见：{role}",
            "challenge": "需检查可替代解释",
            "missing": "反向证据",
            "citations": [e["id"] for e in evidence][:1],
        }


def test_specialists_add_by_role_not_model_refresh_names_and_numeric_ids(tmp_path):
    with TestClient(
        create_app(str(tmp_path / "events.db"), specialist_runner=Runner())
    ) as api:
        root = setup(api)
        assert join(api, root, ["fundamentals"]).is_success
        assert join(api, root).is_success
        assert join(api, root).is_success
        listing = api.get(root + "/specialists").json()["roles"]
        assert {r["id"] for r in listing if r["member_id"]} == set(ROLE_IDS)
        assert all(r["number"].isdigit() for r in listing if r["member_id"])
        assert (
            len([m for m in api.get(root + "/members").json() if m["kind"] == "agent"])
            == 4
        )
        assert len(api.get(root + "/messages").json()) == 0


def test_discussion_writes_distinct_speakers_and_one_chair_across_restart(tmp_path):
    db = str(tmp_path / "events.db")
    r = Runner()
    with TestClient(create_app(db, specialist_runner=r)) as api:
        root = setup(api)
        assert join(api, root).is_success
        result = api.post(root + "/roundtables", json=PACKET)
        assert result.is_success, result.text
        out = result.json()
        assert out["status"] == "completed" and out["excluded_ids"] == ["N1"]
        assert {call[0] for call in r.calls} == set(ROLE_IDS + ["chair"])
        assert all(
            "截止日后材料" not in call[1] and call[2] == ["F1"] for call in r.calls
        )
        assert "独立意见：short_term" in next(x[1] for x in r.calls if x[0] == "chair")
        assert len(api.get(root + "/messages").json()) == 5
    with TestClient(create_app(db, specialist_runner=r)) as api:
        assert api.post(root + "/roundtables", json=PACKET).json() == out
        assert len(api.get(root + "/messages").json()) == 5
        assert len(r.calls) == 5
        assert (
            api.post(
                root + "/roundtables", json={**PACKET, "symbol": "DIFFERENT"}
            ).status_code
            == 409
        )


def test_counterexample_off_group_actor_and_uninstalled_role_do_not_run(tmp_path):
    r = Runner()
    with TestClient(
        create_app(str(tmp_path / "events.db"), specialist_runner=r)
    ) as api:
        root = setup(api)
        assert join(api, root, ["fundamentals"]).is_success
        assert api.post(root + "/roundtables", json=PACKET).status_code == 422
        assert (
            api.post(
                root + "/roundtables",
                json={**PACKET, "actor": "外部人士", "roles": ["fundamentals"]},
            ).status_code
            == 403
        )
        assert not r.calls


def test_counterexample_invalid_citation_is_not_published_as_research(tmp_path):
    r = Runner(bad_role="short_term")
    with TestClient(
        create_app(str(tmp_path / "events.db"), specialist_runner=r)
    ) as api:
        root = setup(api)
        assert join(api, root).is_success
        out = api.post(root + "/roundtables", json=PACKET).json()
        assert out["status"] == "partial"
        assert (
            next(x for x in out["findings"] if x["role"] == "short_term")["status"]
            == "invalid_output"
        )
        assert "不应展示" not in str(api.get(root + "/messages").json())
        assert "不应展示" not in next(x[1] for x in r.calls if x[0] == "chair")


def test_counterexample_browser_from_other_origin_cannot_launch_a_round(tmp_path):
    runner = Runner()
    with TestClient(
        create_app(str(tmp_path / "events.db"), specialist_runner=runner)
    ) as api:
        root = setup(api)
        headers = {"Origin": "https://outside.example"}
        assert (
            api.post(
                root + "/specialists",
                json={"actor": "研究员", "roles": ROLE_IDS},
                headers=headers,
            ).status_code
            == 403
        )
        assert join(api, root).is_success
        assert (
            api.post(root + "/roundtables", json=PACKET, headers=headers).status_code
            == 403
        )
        assert not runner.calls


def test_counterexample_no_runner_no_route(tmp_path):
    with TestClient(create_app(str(tmp_path / "events.db"))) as api:
        root = setup(api)
        assert api.get(root + "/specialists").status_code == 404


def test_counterexample_overlap_cannot_dispatch_same_group_twice(tmp_path):
    entered, release = threading.Event(), threading.Event()
    r = Runner()

    def held(**kw):
        entered.set()
        assert release.wait(5)
        return r(**kw)

    with TestClient(
        create_app(str(tmp_path / "events.db"), specialist_runner=held)
    ) as api, ThreadPoolExecutor(1) as pool:
        root = setup(api)
        assert join(api, root).is_success
        first = pool.submit(api.post, root + "/roundtables", json=PACKET)
        try:
            assert entered.wait(5)
            assert (
                api.post(
                    root + "/roundtables", json={**PACKET, "request_id": "round-test-2"}
                ).status_code
                == 409
            )
        finally:
            release.set()
        assert first.result(5).is_success
        assert len(r.calls) == 5
