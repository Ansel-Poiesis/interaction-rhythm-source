# -*- coding: utf-8 -*-
"""启动自检：判断「已有实例」是否仍在正常采集。

单实例锁被占用时，后台自启/前台启动默认会直接退出。若已有实例其实已经
停止采集（会话结束 paused、锁屏 locked、休眠 sleeping、卡死、长时间无新
数据），直接退出会让采集长期停摆——表现为某一天的使用时长缺失/过短
（例如 8/3 事件：8/2 22:01 会话结束后旧实例进入 paused，8/3 新实例发现
已有实例便退出，全天 0 数据，8/4 10:10 才恢复）。

本模块把「已有实例是否 stale」的判断集中成可测试的纯函数。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime

# 已有实例「最近活动」超过该秒数没更新即视为 stale。
# 正常采集下前台区间、键鼠事件与心跳会持续写入，10 分钟足够宽松。
STALE_ACTIVITY_SECONDS = 10 * 60

# 明确「不采集」的采集器状态（命中即 stale，需要接管）。
NON_COLLECTING_STATES = {"paused", "locked", "sleeping", "degraded", "unknown"}


def parse_iso(value) -> datetime | None:
    """容错解析 ISO 时间字符串，失败返回 None。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _as_aware(dt: datetime) -> datetime:
    """把 naive datetime 补上本地时区，避免与 aware 时间比较报错。"""
    if dt.tzinfo is not None:
        return dt
    local = datetime.now().astimezone().tzinfo
    return dt.replace(tzinfo=local)


def query_status(port: int, timeout: float = 0.5) -> tuple[bool, dict | None]:
    """查询已有实例的 /api/internal/status。

    返回 (reachable, status)：
    - reachable=False：服务无响应（实例卡死/僵死，或端口被无关程序占用）。
    - status=None：服务有响应但版本较旧，没有该端点（如 0.10.2）。
    """
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/internal/status"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                raw = resp.read().decode("utf-8") or "{}"
                return True, json.loads(raw)
            return True, None
    except urllib.error.HTTPError:
        return True, None
    except Exception:
        return False, None


def existing_instance_is_stale(
    reachable: bool,
    status: dict | None,
    local_last_activity: datetime | None,
    now: datetime | None = None,
) -> bool:
    """已有实例是否已停止采集（需要新实例接管）。

    参数：
    - reachable / status：query_status() 的结果。
    - local_last_activity：本数据目录（DB_PATH）最近一条前台/输入活动时间；
      旧版实例没有 status 端点时作为回退信号。None 表示无法判定。
    """
    now = _as_aware(now or datetime.now().astimezone())
    if not reachable:
        # 服务无响应：实例已卡死或僵死，视为 stale。
        return True
    if status is not None:
        state = (status.get("collector_state") or "").lower()
        if state in NON_COLLECTING_STATES:
            return True
        last = parse_iso(status.get("last_activity"))
        if last is not None:
            return (now - _as_aware(last)).total_seconds() > STALE_ACTIVITY_SECONDS
        # 状态为 running 但还没有活动记录 → 刚启动，不视为 stale。
        return False
    # 旧版实例没有 status 端点：回退到本数据目录最近活动。
    if local_last_activity is not None:
        return (now - _as_aware(local_last_activity)).total_seconds() > STALE_ACTIVITY_SECONDS
    # 无法判定时保守返回 False，避免误杀健康实例。
    return False
# 接管冷却：一次接管会终止已有实例（中断其采集）。当 WebView2 反复初始化失败
# 形成空壳窗口时，若每次启动都接管，会形成「接管→重启→空壳→再接管」死循环
# （如 8/5 15:42-16:07 期间 30+ 次重启、约 1 小时无采集）。冷却期内不再接管，
# 直接退出，保留已有实例的采集。
TAKEOVER_COOLDOWN_SECONDS = 15 * 60


def takeover_in_cooldown(
    marker_time_iso: str | None,
    now: datetime | None = None,
    cooldown_seconds: float = TAKEOVER_COOLDOWN_SECONDS,
) -> bool:
    """最近一次接管尝试是否仍在冷却期内。

    marker_time_iso 为空/无法解析时返回 False（未发生过接管或文件损坏，不冷却）。
    """
    if not marker_time_iso:
        return False
    last = parse_iso(str(marker_time_iso))
    if last is None:
        return False
    now = _as_aware(now or datetime.now().astimezone())
    return (now - _as_aware(last)).total_seconds() < cooldown_seconds
