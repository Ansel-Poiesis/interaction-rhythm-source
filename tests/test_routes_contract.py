"""前后端路由合同测试（D8）。

从 static/index.html 提取前端实际调用的 /api/ 端点基路径，
从 stats.py 提取后端注册的路由字面量，断言前端调用 ⊆ 后端路由。
防止新增前端调用却漏写后端路由（或路由改名）导致的静默 404。
"""
import re
import unittest
from pathlib import Path
def _frontend_source() -> str:
    """index.html + 拆分后的 style.css / i18n.js / app.js 拼接（D9 机械三拆后测试仍以整体前端为合同对象）。"""
    static = Path(__file__).resolve().parents[1] / "static"
    parts = []
    for name in ("index.html", "style.css", "i18n.js", "app.js"):
        candidate = static / name
        if candidate.exists():
            parts.append(candidate.read_text(encoding="utf-8"))
    return "\n".join(parts)

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = _frontend_source()
STATS_PY = (REPO_ROOT / "stats.py").read_text(encoding="utf-8")

# 内部端点不通过前端调用，仅本机进程间使用
BACKEND_ONLY = {"/api/internal/status"}


def frontend_endpoints() -> set[str]:
    found = set()
    for match in re.findall(r"/api/[A-Za-z0-9/_-]+", INDEX_HTML):
        base = match.split("?")[0].rstrip("/")
        if base:
            found.add(base)
    return found


def backend_endpoints() -> set[str]:
    return set(re.findall(r"[\"'](/api/[A-Za-z0-9/_-]+)[\"']", STATS_PY))


class RouteContractTests(unittest.TestCase):
    def test_every_frontend_endpoint_has_backend_route(self):
        backend = backend_endpoints()
        missing = sorted(frontend_endpoints() - backend)
        self.assertEqual(
            missing,
            [],
            f"前端调用了后端未注册的端点: {missing}",
        )

    def test_backend_route_table_not_shrunk(self):
        # 后端路由面基线：删除任何路由都应在此显式更新基线并说明理由
        backend = backend_endpoints()
        self.assertGreaterEqual(len(backend), 38)
        for required in (
            "/api/today",
            "/api/day",
            "/api/heatmap",
            "/api/apps",
            "/api/insights",
            "/api/agent/observations",
            "/api/agent/day",
            "/api/agent/jobs",
            "/api/settings",
            "/api/data/backup",
            "/api/data/restore",
            "/api/feedback",
            "/api/update/status",
        ):
            self.assertIn(required, backend)

    def test_backend_only_endpoints_documented(self):
        frontend = frontend_endpoints()
        for endpoint in BACKEND_ONLY:
            self.assertNotIn(endpoint, frontend)


if __name__ == "__main__":
    unittest.main()
