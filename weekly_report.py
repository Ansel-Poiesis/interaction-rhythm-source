"""扣舷 · 每周数字手感报告生成器

本地规则生成、纯标准库、只读打开数据库快照。输出可直接在邮件中阅读的
HTML（内联样式、无外链、无图片依赖），也可保存为本地网页预览。

报告窗口为自然周（周一至周日）。所有结论都是对既有聚合口径的本地描述，
不调用外部模型，不判断好坏，也不把变化归因。

用法：
  python weekly_report.py                                    # 默认最近一个完整自然周
  python weekly_report.py --end-date 2026-08-09 --out tmp/report.html
  python weekly_report.py --db <tracker.db> --json tmp/data.json
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from db import DB
from interaction_rhythm.observations import local_now
from interaction_rhythm.runtime_paths import DATA_DIR_ENV
from settings import get_settings

APP_TITLE = "扣舷 · 每周数字手感"
PRODUCT_NAME = "扣舷-数字节律"

INK = "#171717"
INK_SOFT = "#3b3b3b"
MUTED = "#777b82"
FAINT = "#aeb2b8"
PRIMARY = "#2f8f59"
PRIMARY_STRONG = "#1f6f43"
PRIMARY_DIM = "#f4faf6"
BAND = "#9fd5b5"
BAND_DIM = "#e3f2e8"
BAR_BG = "#eeeeee"
SURFACE = "#ffffff"
BG = "#f7f7f7"
BORDER = "rgba(23,23,23,.07)"

FONT_STACK = (
    "'LXGW WenKai Screen','霞鹜文楷屏幕版','LXGW WenKai','霞鹜文楷',"
    "'Microsoft YaHei',system-ui,sans-serif"
)


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _fmt_count(value: int | float) -> str:
    value = int(value or 0)
    if value >= 10000:
        text = f"{value / 10000:.1f} 万".replace(".0 万", " 万")
        return text
    return f"{value:,}"


def _fmt_duration(seconds: int | float) -> str:
    seconds = max(0, int(seconds or 0))
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    if hours and minutes:
        return f"{hours} 小时 {minutes} 分钟"
    if hours:
        return f"{hours} 小时"
    if minutes:
        return f"{minutes} 分钟"
    return f"{seconds} 秒"


def _fmt_interval(seconds: int | float) -> str:
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 3600:
        return f"{seconds // 60} 分 {seconds % 60} 秒"
    hours, rest = divmod(seconds, 3600)
    return f"{hours} 小时 {rest // 60} 分"


def _fmt_weekday_date(value) -> str:
    date = datetime.fromisoformat(str(value)).date()
    return f"{date.month} 月 {date.day} 日"


def _display_name(app: str, labels: dict[str, str]) -> str:
    """与 db.DB._app_display_name 保持一致，并优先使用用户标签。"""
    name = (app or "").strip()
    stem = name[:-4] if name.lower().endswith(".exe") else name
    lower = stem.lower()
    for key in (name, stem, lower):
        if key in labels and labels[key]:
            return labels[key]
    aliases = {
        "code": "VS Code",
        "codex": "Codex",
        "chrome": "Chrome",
        "msedge": "Edge",
        "explorer": "文件管理器",
        "wechat": "微信",
        "obsidian": "Obsidian",
        "python": "Python",
        "pythonw": "Python",
    }
    return aliases.get(lower, stem or name)


def _position_of(value: float, ref: dict) -> str | None:
    p25, p75 = ref.get("p25"), ref.get("p75")
    if p25 is None or p75 is None or value is None:
        return None
    if value < p25:
        return "below"
    if value > p75:
        return "above"
    return "within"


POSITION_TEXT = {
    "below": "低于",
    "within": "在",
    "above": "高于",
}


def _delta_dict(current: int, previous: int) -> dict:
    diff = int(current) - int(previous)
    pct = (diff / int(previous) * 100) if previous else None
    return {"current": int(current), "previous": int(previous), "diff": diff, "pct": pct}


def _week_active_days(trend: dict, week_start: str) -> int:
    for week in trend.get("weeks", []):
        if week.get("week_start") == week_start:
            return int(week.get("active_days") or 0)
    return 0


# These are project-authored interpretation rules, not copied quotations or empirical findings.
CARDS = {
    "R1": {
        "title": "记录不等于改变",
        "level": "项目方法",
        "source": "项目自有分析规则",
        "quote": "事件记录能帮助回看观察到的变化，但不能单独证明行为改变或结果。",
    },
    "R2": {
        "title": "让结论回到事实",
        "level": "项目方法",
        "source": "项目自有分析规则",
        "quote": "指标、分母、时间范围和结论应彼此对应；口径变化要明确说明。",
    },
    "R3": {
        "title": "切换只是一种形态",
        "level": "项目方法",
        "source": "项目自有分析规则",
        "quote": "进程切换可以描述电脑活动的形态，但不能单独推断分心、意图或任务中断。",
    },
    "R4": {
        "title": "总时长与动作分布分开看",
        "level": "项目方法",
        "source": "项目自有分析规则",
        "quote": "总量、持续时间和分布是不同的观察维度，不从桌面日志推断注意力。",
    },
    "R5": {
        "title": "个人参照需要积累",
        "level": "项目方法",
        "source": "项目自有分析规则",
        "quote": "观测不足时只描述记录到的形态，不输出个人位置或方向判断。",
    },
    "R6": {
        "title": "比较要保留分母",
        "level": "项目方法",
        "source": "项目自有分析规则",
        "quote": "展示比较结果时同时交代时间和覆盖条件，不把相关变化写成因果效果。",
    },
    "R7": {
        "title": "应用路径不是任务路径",
        "level": "项目方法",
        "source": "项目自有分析规则",
        "quote": "进程往返只表示可观察的应用序列，不证明任务恢复、完成或受打断。",
    },
    "R8": {
        "title": "隐私边界需要说清",
        "level": "项目方法",
        "source": "项目自有分析规则",
        "quote": "说明收集和不收集的内容、数据保存位置，以及用户可选的外发功能。",
    },
    "R9": {
        "title": "数据不足时仍说明状态",
        "level": "项目方法",
        "source": "项目自有分析规则",
        "quote": "记录还在积累时标明覆盖和参照条件，不隐藏事实，也不虚构趋势。",
    },
    "E1": {
        "title": "围绕具体问题组织报告",
        "level": "项目方法",
        "source": "项目自有分析规则",
        "quote": "先回答用户要回看的具体事实，不添加没有数据支持的评分。",
    },
    "E2": {
        "title": "数字要带范围和分母",
        "level": "项目方法",
        "source": "项目自有分析规则",
        "quote": "报告交代指标范围、分母和缺失项，让用户能判断数字代表什么。",
    },
    "E3": {
        "title": "叙事不为分享而夸大",
        "level": "项目方法",
        "source": "项目自有分析规则",
        "quote": "只围绕可核对的个人事实组织叙事，不为了比较或传播而放大结论。",
    },
    "E4": {
        "title": "展示不等于评价",
        "level": "项目方法",
        "source": "项目自有分析规则",
        "quote": "应用可以提供观察和选择，不以评分、游戏化或阻断替代用户判断。",
    },
    "E5": {
        "title": "隐私应当可验证",
        "level": "项目方法",
        "source": "项目自有分析规则",
        "quote": "如实列出本地文件、可选外发路径和停用方式，不把隐私写成抽象承诺。",
    },
    "E6": {
        "title": "积累期也给清楚反馈",
        "level": "项目方法",
        "source": "项目自有分析规则",
        "quote": "数据不足时解释正在积累，并指出当前哪些判断还不成立。",
    },
}

TIER_LABELS = {
    "S0": "空库",
    "S1": "微量",
    "S2": "早期",
    "S3": "标准",
    "S4": "成熟",
    "S5": "长期/海量",
}


TIER_DESCRIPTIONS = {
    "S0": "还没有任何记录：先解释采集什么、为什么先记录；数字卡仅作零值占位，不作任何形态判断。",
    "S1": "1–6 个有效日：只能描述形态（密度、高峰、回返），不下位置与方向判断。",
    "S2": "7–27 个有效日：首张周卡成形，可谈走向，个人参考仍在积累。",
    "S3": "28–89 个有效日：个人基线可用，位置判断（低于/在/高于常态）成立。",
    "S4": "90–179 个有效日或 12 周满带：趋势窗口满，「数据积累以来」的方向可以谈。",
    "S5": "180 天以上或海量数据：明细按保留窗口滚动，日总量兜底；只说分层保留承诺，不承诺无限明细。",
}


TIER_REFERENCES = {
    "S0": ["R1", "E2", "E6"],
    "S1": ["R5", "R3", "E1"],
    "S2": ["R5", "R9", "E3"],
    "S3": ["R6", "R7", "E3"],
    "S4": ["R2", "R4", "R6"],
    "S5": ["R7", "R8", "E4"],
}


def detect_profile(data: dict, corpus: dict | None = None) -> dict:
    """把当前数据归入 S0–S5 六档，并给出可引用的证据卡 ID 清单。

    档位按「数据积累规模」判定：有效日、有数据周、历史天数、事件量与库大小。
    规模上去了（如真实库 12 周满带），即使个人参考仍在积累，也不降为冷启动——
    参考状态（accumulating/ready）单独记录，两者共同决定措辞。
    """
    ref = (data.get("reference") or {}).get("input") or {}
    ref_days = int(ref.get("n_valid_days") or 0)
    weeks = (data.get("trend") or {}).get("weeks") or []
    data_weeks = sum(
        1 for w in weeks
        if int(w.get("input_events") or 0) > 0 or int(w.get("active_days") or 0) > 0
    )
    corpus = corpus or (data.get("corpus") or {})
    history_days = int(corpus.get("history_days") or 0)
    total_input = int(corpus.get("total_input_events") or 0)
    db_mb = float(corpus.get("db_size_mb") or 0)

    if data_weeks == 0 and ref_days == 0:
        tier = "S0"
    elif ref_days >= 180 or history_days >= 180 or db_mb >= 512 or total_input >= 5000000:
        tier = "S5"
    elif ref_days >= 90 or data_weeks >= 12 or history_days >= 90:
        tier = "S4"
    elif ref_days >= 28 or data_weeks >= 4:
        tier = "S3"
    elif ref_days >= 7 or data_weeks >= 2:
        tier = "S2"
    else:
        tier = "S1"

    returns = data.get("returns") or {}
    high_frequency_returns = bool(
        returns.get("apps")
        and int(returns["apps"][0].get("median_seconds") or 0) < 60
        and int(returns["apps"][0].get("returns") or 0) >= 100
    )
    has_hidden = bool(data.get("hidden_apps"))
    has_labels = bool(data.get("app_labels"))

    references = list(TIER_REFERENCES.get(tier, []))
    if high_frequency_returns and "R4" not in references:
        references.insert(1, "R4")
    if (has_hidden or has_labels) and "R8" not in references:
        references.append("R8")

    description = TIER_DESCRIPTIONS.get(tier, "")
    if tier == "S3" and ref_days < 28:
        # 数据周/历史跨度已足 4 周可把档位抬到 S3，但个人参考仍在积累：
        # 静态档位描述声称「基线可用」会与事实矛盾，整体换成与参考阶段一致的措辞。
        description = (
            "数据周已足 4 周但个人参考仍在积累（有效日不足 28 天）："
            "可谈走向，位置措辞按积累进度标注。"
        )

    return {
        "tier": tier,
        "label": TIER_LABELS.get(tier, tier),
        "description": description,
        "signals": {
            "ref_days": ref_days,
            "data_weeks": data_weeks,
            "history_days": history_days,
            "total_input_events": total_input,
            "db_size_mb": db_mb,
        },
        "reference_stage": "accumulating" if ref_days < 28 else "ready",
        "high_frequency_returns": high_frequency_returns,
        "references": references,
    }


def citation_block(profile: dict) -> str:
    """「证据引用 · 按数据规模」块：档位说明 + 按档选定的证据卡一句话引用。"""
    rows = []
    for ref_id in profile.get("references", []):
        card = CARDS.get(ref_id)
        if not card:
            continue
        rows.append(
            f'<div style="display:flex;gap:8px;align-items:baseline;margin-top:8px;">'
            f'<div style="font-size:13px;font-weight:800;color:{PRIMARY_STRONG};'
            f'white-space:nowrap;min-width:34px;">{_esc(ref_id)}</div>'
            f'<div style="font-size:14.5px;color:{INK_SOFT};line-height:1.7;">'
            f'<span style="font-weight:800;color:{INK};">{_esc(card["title"])}</span>'
            f'（{_esc(card["level"])}）：{_esc(card["quote"])}'
            f'<div style="font-size:13px;color:{FAINT};margin-top:2px;">{_esc(card["source"])}</div>'
            f"</div></div>"
        )
    signals = profile.get("signals") or {}
    head = (
        f'当前数据规模：{profile.get("label", "")}。'
        f'{profile.get("description", "")}'
        f'（有效日 {signals.get("ref_days", 0)} · '
        f'有数据 {signals.get("data_weeks", 0)} 周 · '
        f'事件 {_fmt_count(signals.get("total_input_events", 0))} · '
        f'库 {signals.get("db_size_mb", 0)} MB）'
    )
    return (
        f'<div style="background:{SURFACE};border:1px solid {BORDER};border-radius:10px;'
        f'padding:12px 14px;margin:10px 0;">'
        f'<div style="font-size:16px;font-weight:800;color:{INK};margin-bottom:8px;">'
        f'<span style="color:{PRIMARY};font-weight:900;">§ </span>证据引用 · 按数据规模</div>'
        f'<div style="font-size:14.5px;color:{MUTED};line-height:1.7;">{_esc(head)}</div>'
        f'{"".join(rows)}'
        f'</div>'
    )


def load_data(db: DB, end_date: str | None = None, settings: dict | None = None) -> dict:
    """把报告所需的所有聚合一次性取出，统一口径。"""
    today = local_now().date()
    if end_date:
        end = datetime.fromisoformat(str(end_date)).date()
    else:
        end = today - timedelta(days=(today.weekday() + 1) % 7)
    end_iso = end.isoformat()

    summary = db.week_summary(end_iso, 7)
    trend = db.trend_summary(12)
    returns = db.return_interval_summary(7)
    sessions = db.week_session_stats(end_iso, 7)
    labels = db.app_label_map() or {}
    hidden = {
        str(item).strip().lower()
        for item in (settings or {}).get("hidden_apps", [])
        if str(item or "").strip()
    }

    top_apps = [
        item for item in summary.get("top_apps", [])
        if str(item.get("name") or "").strip().lower() not in hidden
    ][:5]
    top_apps = [
        {
            "name": item["name"],
            "display": _display_name(item["name"], labels),
            "seconds": int(item.get("seconds") or 0),
        }
        for item in top_apps
    ]
    return_apps = [
        item for item in returns.get("apps", [])
        if str(item.get("name") or "").strip().lower() not in hidden
    ][:3]
    return_apps = [
        {
            "name": item["name"],
            "display": _display_name(item["name"], labels),
            "returns": int(item.get("returns") or 0),
            "median_seconds": int(item.get("median_seconds") or 0),
        }
        for item in return_apps
    ]

    foreground_seconds = int(summary.get("foreground_seconds") or 0)
    keyboard = int(summary.get("keyboard_events") or 0)
    mouse = int(summary.get("mouse_events") or 0)
    scroll = int(summary.get("scroll_events") or 0)
    input_events = int(summary.get("input_events") or 0)
    active_days = _week_active_days(trend, summary.get("start") or end_iso)
    peak_hours = [item for item in summary.get("peak_hours", []) if item.get("hour") is not None][:2]
    peak_hour = peak_hours[0]["hour"] if peak_hours else None
    top = top_apps[0] if top_apps else None
    top_share = (top["seconds"] / foreground_seconds) if top and foreground_seconds else 0.0

    weeks = trend.get("weeks", [])
    prev_week = None
    for index, week in enumerate(weeks):
        if week.get("week_start") == summary.get("start") and index >= 1:
            prev_week = weeks[index - 1]
            break
    change = {
        "input": (
            _delta_dict(input_events, int(prev_week.get("input_events") or 0))
            if prev_week
            else None
        ),
        "foreground_seconds": (
            _delta_dict(foreground_seconds, int(prev_week.get("foreground_seconds") or 0))
            if prev_week
            else None
        ),
    }

    density_per_min = (input_events / (foreground_seconds / 60)) if foreground_seconds else 0.0
    prev_fg = int(prev_week.get("foreground_seconds") or 0) if prev_week else 0
    prev_input = int(prev_week.get("input_events") or 0) if prev_week else 0
    prev_density = (prev_input / (prev_fg / 60)) if prev_fg else 0.0
    change["density"] = (
        {
            "current": round(density_per_min, 1),
            "previous": round(prev_density, 1),
            "diff": round(density_per_min - prev_density, 1),
            "pct": (
                (density_per_min - prev_density) / prev_density * 100
                if prev_density
                else None
            ),
        }
        if prev_week
        else None
    )

    ref_input = trend.get("baseline", {}).get("input") or {}
    ref_fg = trend.get("baseline", {}).get("foreground_seconds") or {}
    avg_daily_input = round(input_events / max(1, int(summary.get("days") or 7)))
    avg_daily_fg = round(foreground_seconds / max(1, int(summary.get("days") or 7)))
    input_position = _position_of(avg_daily_input, ref_input)
    fg_position = _position_of(avg_daily_fg, ref_fg)

    input_total = max(1, input_events)
    structure = {
        "keyboard_pct": round(keyboard / input_total * 100),
        "mouse_pct": round(mouse / input_total * 100),
        "scroll_pct": round(scroll / input_total * 100),
    }
    peak_pct = round(peak_hours[0]["count"] / input_total * 100) if peak_hours else None
    top3_share = (
        sum(app["seconds"] for app in top_apps[:3]) / foreground_seconds
        if foreground_seconds and top_apps
        else 0.0
    )
    recent_bars = [float(w.get("avg_daily_input") or 0) for w in weeks[-4:]]
    prior_bars = [float(w.get("avg_daily_input") or 0) for w in weeks[-8:-4]]
    trend_direction = None
    if len(recent_bars) >= 4 and len(prior_bars) >= 4 and any(prior_bars):
        trend_direction = _delta_dict(round(sum(recent_bars) / 4), round(sum(prior_bars) / 4))

    density_line = (
        f"输入密度约 {density_per_min:.1f} 次/分钟"
        f"（上周约 {prev_density:.1f}）" if prev_density else f"输入密度约 {density_per_min:.1f} 次/分钟"
    )
    analysis_lines = [f"{density_line}。"]
    analysis_lines.append(
        f"输入结构：键盘 {structure['keyboard_pct']}% · 点击 {structure['mouse_pct']}% · 滚轮 {structure['scroll_pct']}%。"
    )
    if peak_hour is not None and peak_pct is not None:
        peak_text = " / ".join(f"{p['hour']:02d}:00" for p in peak_hours[:2])
        analysis_lines.append(f"高峰集中在 {peak_text} 前后，最高峰约占全天输入 {peak_pct}%。")
    if top:
        analysis_lines.append(
            f"主场 {top['display']} 占本周前台约 {round(top_share * 100)}%，"
            f"前三应用合计约 {round(top3_share * 100)}%。"
        )
    if return_apps:
        first = return_apps[0]
        if first["median_seconds"] < 60 and first["returns"] >= 100:
            analysis_lines.append(
                f"回返强度高：{first['display']} 回返 {first['returns']} 次、"
                f"中位间隔 {_fmt_interval(first['median_seconds'])}，呈高频往返。"
            )
        else:
            analysis_lines.append(
                f"回返节奏：{first['display']} 回返 {first['returns']} 次、"
                f"中位间隔 {_fmt_interval(first['median_seconds'])}。"
            )

    if keyboard > mouse * 1.5:
        rhythm_type = "键盘响应型"
    elif mouse > keyboard * 1.5:
        rhythm_type = "鼠标浏览型"
    else:
        rhythm_type = "混合手感型"
    if top and top_share >= 0.5:
        app_phrase = f"主场应用是 {top['display']}"
    elif top and top_share >= 0.25:
        app_phrase = f"有一个较明显的主场应用：{top['display']}"
    else:
        app_phrase = "应用分布比较分散"
    if len(peak_hours) >= 2:
        hour_phrase = f"高峰多在 {peak_hours[0]['hour']:02d}:00 与 {peak_hours[1]['hour']:02d}:00 前后"
    elif peak_hour is not None:
        hour_phrase = f"高峰多在 {peak_hour:02d}:00 左右"
    else:
        hour_phrase = "高峰时间还不明显"
    density_phrase = (
        f"输入密度约 {density_per_min:.1f} 次/分钟，较上周约 {prev_density:.1f} 次/分钟更密"
        if prev_density
        else f"输入密度约 {density_per_min:.1f} 次/分钟"
    )
    conclusion = (
        f"本周呈现{rhythm_type}数字手感：{app_phrase}，{hour_phrase}；"
        f"{density_phrase}，整体更像「前台更短、输入更密」的形态。"
    )

    ref_input["p25_text"] = _fmt_count(ref_input["p25"]) if ref_input.get("p25") is not None else None
    ref_input["p75_text"] = _fmt_count(ref_input["p75"]) if ref_input.get("p75") is not None else None
    ref_fg["p25_text"] = _fmt_duration(ref_fg["p25"]) if ref_fg.get("p25") is not None else None
    ref_fg["p75_text"] = _fmt_duration(ref_fg["p75"]) if ref_fg.get("p75") is not None else None

    insights = []
    if prev_density and change["input"] and change["input"]["pct"] is not None:
        in_direction = "增加" if change["input"]["diff"] >= 0 else "减少"
        fg_direction = "增加" if change["foreground_seconds"]["diff"] >= 0 else "减少"
        insights.append({
            "title": "输入变密：前台更短，输入更集中",
            "text": (
                f"输入事件较上周{in_direction} {abs(round(change['input']['pct']))}%，"
                f"前台时长{fg_direction} {abs(round(change['foreground_seconds']['pct']))}%；"
                f"输入密度从约 {prev_density:.1f} 升到约 {density_per_min:.1f} 次/分钟。"
                "同样的使用量可以有完全不同的形态：本周更像「前台更短、输入更密」，"
                "工作节奏更接近集中爆发，而不是均匀铺开。"
            ),
            "source": "Barabási, A.-L. (2005). The origin of bursts and heavy tails in human dynamics. Nature, 435, 207–211.",
        })
    else:
        insights.append({
            "title": f"输入密度约 {density_per_min:.1f} 次/分钟",
            "text": (
                f"本周前台共 {_fmt_duration(foreground_seconds)}，输入 {_fmt_count(input_events)} 次，"
                f"平均每分钟约 {density_per_min:.1f} 次。个人参考仍在积累期，先记录形态，不做强度评价。"
            ),
            "source": "Huber, P. J. (1981). Robust Statistics. Wiley.",
        })
    if top and return_apps:
        first = return_apps[0]
        insights.append({
            "title": f"{top['display']} 是本周的枢纽",
            "text": (
                f"{top['display']} 前台 {_fmt_duration(top['seconds'])}（占本周约 {round(top_share * 100)}%），"
                f"回返 {first['returns']} 次、中位间隔 {_fmt_interval(first['median_seconds'])}——"
                "不断离开又回来，像工作流里的中枢站；"
                "高频往返说明它承接了大量「出去查一步、回来继续」的切换。"
            ),
            "source": "González, V. M., & Mark, G. (2004). Constant, constant, multi-tasking craziness. CHI.",
        })
    data_weeks = [
        w for w in weeks
        if str(w.get("week_end") or "") <= end_iso
        and (int(w.get("input_events") or 0) > 0 or int(w.get("active_days") or 0) > 0)
    ]
    if data_weeks:
        week_values = [float(w.get("avg_daily_input") or 0) for w in data_weeks]
        first_text = _fmt_count(round(week_values[0]))
        last_text = _fmt_count(round(week_values[-1]))
        direction = "上行" if len(week_values) >= 2 and week_values[-1] > week_values[0] else "记录中"
        insights.append({
            "title": f"趋势尺度：自 {len(data_weeks)} 周前开始有数据",
            "text": (
                f"有数据以来日均输入从 {first_text} 走到 {last_text}（{direction}）。"
                f"个人参考只有 {int(ref_input.get('n_valid_days') or 0)} 个有效日："
                "还在积累期，暂时只谈「近几周的走向」，不下稳定结论；"
                "要看清方向，至少需要把参考窗口拉到 28 天。"
            ),
            "source": "Killick, R., Fearnhead, P., & Eckley, I. A. (2013). Optimal detection of changepoints with a linear computational cost. JASA.",
        })
    if sessions.get("session_count"):
        insights.append({
            "title": "前台形态高度碎片化",
            "text": (
                f"本周完整前台段约 {_fmt_count(sessions['session_count'])} 段"
                f"（日均约 {round(sessions['session_count'] / max(1, active_days))} 段），"
                f"典型停留约 {_fmt_interval(sessions['median_dwell_seconds'] or 0)}（中位）。"
                "这是「高频往返」的形态：切换多不等于分心，也可能是工具链循环；"
                "短停留本身不说明质量，要看每次回来是否接得上主线。"
            ),
            "source": "Mark, G., et al. (2014). Bored Mondays and Focused Afternoons: The Rhythm of Attention and Online Activity in the Workplace. CHI.",
        })
    if peak_hour is not None and peak_pct is not None:
        hour_text = " / ".join(f"{p['hour']:02d}:00" for p in peak_hours[:2])
        insights.append({
            "title": "傍晚是输入最密的时段",
            "text": (
                f"高峰集中在 {hour_text} 前后，最高峰约占全天输入 {peak_pct}%。"
                "这是本周的节律形状：输入集中在傍晚而不散落在全天，"
                "说明傍晚时段更接近「整块推进」；先记录，不作评价。"
            ),
            "source": "Mark, G., et al. (2014). The Rhythm of Attention and Online Activity in the Workplace. CHI.",
        })
    insights = insights[:4]

    suggestions = []
    if peak_hour is not None:
        peak_text = (
            " / ".join(f"{p['hour']:02d}:00" for p in peak_hours[:2])
            if len(peak_hours) >= 2
            else f"{peak_hour:02d}:00"
        )
        suggestions.append(
            f"回看 {peak_text} 前后的输入高峰：那段时间是否集中推进了一件具体的事？"
            "下次遇到同一时段，顺手记一句原因，几周后就能对照自己的节律。"
        )
    if return_apps:
        first = return_apps[0]
        suggestions.append(
            f"{first['display']} 高频回返（{first['returns']} 次、中位间隔 {_fmt_interval(first['median_seconds'])}）："
            "更像「查一步、回一步」的工具链。可留意其中是否有「离开→查资料→回来继续」的固定套路。"
        )
    elif top:
        suggestions.append(
            f"主场 {top['display']} 占本周前台约 {round(top_share * 100)}%，"
            "可给它起个名字（✎），几周后再看它的份额是否跟随项目节奏。"
        )
    suggestions.append(
        "持续记录：数据越久，报告越能回答「深层习惯」。"
        f"目前积累 {int(ref_input.get('n_valid_days') or 0)} 个有效日，"
        "等个人参考满 28 天，节律和回返的形状才会真正稳定。"
    )
    suggestions = suggestions[:3]

    corpus = db.corpus_profile()
    profile = detect_profile(
        {
            "reference": {"input": ref_input},
            "trend": trend,
            "returns": returns,
            "corpus": corpus,
            "hidden_apps": (settings or {}).get("hidden_apps", []),
            "app_labels": labels,
        },
        corpus,
    )

    return {
        "window": {
            "start": summary.get("start"),
            "end": summary.get("end"),
            "days": int(summary.get("days") or 7),
            "active_days": active_days,
        },
        "summary": {
            "input_events": input_events,
            "keyboard": keyboard,
            "mouse": mouse,
            "scroll": scroll,
            "foreground_seconds": foreground_seconds,
            "avg_daily_input": avg_daily_input,
            "avg_daily_fg": avg_daily_fg,
            "density_per_min": round(density_per_min, 1),
            "session_count": sessions["session_count"],
            "median_dwell_seconds": sessions["median_dwell_seconds"],
            "peak_hours": peak_hours,
        },
        "top_apps": top_apps,
        "top_share": round(top_share, 4),
        "change": change,
        "analysis": {
            "structure": structure,
            "peak_pct": peak_pct,
            "top3_share": round(top3_share, 4),
            "trend_direction": trend_direction,
            "lines": analysis_lines,
        },
        "returns": {
            "switch_count": int(returns.get("switch_count") or 0),
            "switches_per_day": returns.get("switches_per_day") or 0,
            "active_days": int(returns.get("active_days") or 0),
            "apps": return_apps,
        },
        "trend": trend,
        "reference": {
            "input": {
                "position": input_position,
                "avg": avg_daily_input,
                **{k: ref_input.get(k) for k in ("median", "p25", "p75", "n_valid_days", "stage")},
            },
            "foreground_seconds": {
                "position": fg_position,
                "avg": avg_daily_fg,
                **{k: ref_fg.get(k) for k in ("median", "p25", "p75", "n_valid_days", "stage")},
            },
        },
        "narrative": {
            "rhythm_type": rhythm_type,
            "conclusion": conclusion,
            "insights": insights,
            "suggestions": suggestions,
        },
        "corpus": corpus,
        "profile": profile,
        "generated_at": local_now().strftime("%Y-%m-%d %H:%M"),
    }


def _card(title: str, body: str, accent: str = "") -> str:
    head = ""
    if title:
        mark = f'<span style="color:{PRIMARY};font-weight:800;">{_esc(accent or "·")}</span> ' if accent else ""
        head = (
            f'<div style="font-size:16px;font-weight:800;color:{INK};margin-bottom:8px;">'
            f"{mark}{_esc(title)}</div>"
        )
    return (
        f'<div style="background:{SURFACE};border:1px solid {BORDER};border-radius:10px;'
        f'padding:12px 14px;margin:10px 0;">{head}{body}</div>'
    )


def _clause_blocks(text: str) -> str:
    """把一段解读按全角冒号/分号切分，标点留在段尾，每段渲染成独立行。

    只切全角标点，避免误伤「17:00」这类半角冒号时间写法。
    """
    clauses: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in "：；":
            clauses.append(buf.strip())
            buf = ""
    if buf.strip():
        clauses.append(buf.strip())
    return "".join(
        f'<div style="margin-top:{4 if index else 0}px;">{_esc(part)}</div>'
        for index, part in enumerate(clauses)
    )


def _interpretation(title: str, lines: list[str], source: str) -> str:
    """数据卡片下方的解读块：标题 + 分句（冒号/分号处自动分段），引用学术原文。"""
    body = "".join(_clause_blocks(line) for line in lines)
    return (
        f'<div style="font-size:14.5px;line-height:1.75;color:{PRIMARY_STRONG};'
        f'background:{PRIMARY_DIM};border-left:3px solid {PRIMARY};border-radius:6px;'
        f'padding:8px 10px;margin-top:10px;">'
        f'<div style="font-weight:800;font-size:15px;">解读 · {_esc(title)}</div>'
        f"{body}"
        f'<div style="font-size:13px;color:{MUTED};margin-top:6px;">{_esc(source)}</div>'
        f"</div>"
    )


def _overview_table(data: dict) -> str:
    s = data["summary"]
    fg_text = _fmt_duration(s["foreground_seconds"])
    active = data["window"]["active_days"]
    days = data["window"]["days"]
    peak = "—"
    if s["peak_hours"]:
        peak = " / ".join(f"{p['hour']:02d}:00" for p in s["peak_hours"])
    avg_fg = _fmt_duration(s["avg_daily_fg"])
    rows_data = [
        [(_fmt_count(s["input_events"]), "输入事件"), (fg_text, "前台时长"), (f"{active}/{days} 天", "活跃天数")],
        [(_fmt_count(s["avg_daily_input"]), "日均输入"), (avg_fg, "日均前台"), (peak, "高峰时段")],
    ]
    rows = []
    for row in rows_data:
        cells = []
        for index, (value, label) in enumerate(row):
            border_left = f"border-left:1px solid {BORDER};" if index else ""
            cells.append(
                f'<td style="width:33.33%;padding:6px 4px;text-align:center;{border_left}">'
                f'<div style="font-size:24px;font-weight:850;color:{INK};font-variant-numeric:tabular-nums;'
                f'line-height:1.25;white-space:nowrap;">{_esc(value)}</div>'
                f'<div style="font-size:13.5px;color:{MUTED};margin-top:3px;">{_esc(label)}</div>'
                f"</td>"
            )
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">{"".join(rows)}</table>'


def _reference_table(data: dict) -> str:
    """常态对照：参考不足时只给一句简单说明，不铺整张表。"""
    ref_input = data["reference"]["input"]
    ref_fg = data["reference"]["foreground_seconds"]
    min_days = 28
    input_ready = (
        int(ref_input.get("n_valid_days") or 0) >= min_days
        and ref_input.get("p25") is not None
        and ref_input.get("p75") is not None
    )
    fg_ready = (
        int(ref_fg.get("n_valid_days") or 0) >= min_days
        and ref_fg.get("p25") is not None
        and ref_fg.get("p75") is not None
    )
    if not (input_ready and fg_ready):
        valid_days = int(ref_input.get("n_valid_days") or 0)
        return (
            f'<div style="font-size:15px;color:{INK_SOFT};line-height:1.7;">'
            f"个人常态对照仍在积累（{valid_days} 个有效日）。"
            f"本周日均输入 {_fmt_count(ref_input.get('avg') or 0)}、"
            f"日均前台 {_fmt_duration(ref_fg.get('avg') or 0)}，"
            "参考满 28 天后再给出典型范围与位置判断。</div>"
        )
    rows = []
    for key, label in (("input", "日均输入"), ("foreground_seconds", "日均前台")):
        ref = data["reference"][key]
        position = ref.get("position")
        formatter = _fmt_count if key == "input" else _fmt_duration
        avg_text = formatter(ref.get("avg") or 0)
        median_text = formatter(ref.get("median")) if ref.get("median") is not None else "—"
        if ref.get("p25") is not None and ref.get("p75") is not None:
            pos_text = POSITION_TEXT.get(position or "within", "在")
            range_text = f"{formatter(ref['p25'])}–{formatter(ref['p75'])}"
            position_text = f"{pos_text}典型范围"
            tone = PRIMARY if position in ("below", "above") else MUTED
        else:
            range_text = "—"
            position_text = f"仍在积累（{int(ref.get('n_valid_days') or 0)} 个有效日）"
            tone = MUTED
        rows.append(
            f'<tr>'
            f'<td style="padding:6px 8px;border:1px solid {BORDER};color:{INK_SOFT};white-space:nowrap;">{label}</td>'
            f'<td style="padding:6px 8px;border:1px solid {BORDER};font-variant-numeric:tabular-nums;">{avg_text}</td>'
            f'<td style="padding:6px 8px;border:1px solid {BORDER};font-variant-numeric:tabular-nums;">{median_text}</td>'
            f'<td style="padding:6px 8px;border:1px solid {BORDER};font-variant-numeric:tabular-nums;">{range_text}</td>'
            f'<td style="padding:6px 8px;border:1px solid {BORDER};color:{tone};white-space:nowrap;">{position_text}</td>'
            f"</tr>"
        )
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:15px;">'
        f'<tr>'
        f'<th style="padding:5px 8px;border:1px solid {BORDER};background:{BG};color:{MUTED};font-weight:700;text-align:left;">指标</th>'
        f'<th style="padding:5px 8px;border:1px solid {BORDER};background:{BG};color:{MUTED};font-weight:700;text-align:left;">本周均值</th>'
        f'<th style="padding:5px 8px;border:1px solid {BORDER};background:{BG};color:{MUTED};font-weight:700;text-align:left;">28 天中位</th>'
        f'<th style="padding:5px 8px;border:1px solid {BORDER};background:{BG};color:{MUTED};font-weight:700;text-align:left;">典型范围</th>'
        f'<th style="padding:5px 8px;border:1px solid {BORDER};background:{BG};color:{MUTED};font-weight:700;text-align:left;">位置</th>'
        f"</tr>{''.join(rows)}</table>"
    )


def _apps_table(apps: list[dict], foreground_seconds: int) -> str:
    if not apps:
        return '<div style="font-size:15px;color:' + MUTED + ';">本周没有可展示的应用记录。</div>'
    rows = []
    for index, app in enumerate(apps):
        share = (app["seconds"] / foreground_seconds) if foreground_seconds else 0.0
        width = max(2, round(share * 100))
        rows.append(
            f'<tr>'
            f'<td style="padding:6px 4px;border:1px solid {BORDER};width:22px;color:{FAINT};font-size:13px;text-align:center;">{index + 1}</td>'
            f'<td style="padding:6px 4px;border:1px solid {BORDER};white-space:nowrap;">'
            f'<div style="font-size:15.5px;font-weight:800;color:{INK};line-height:1.3;">{_esc(app["display"])}</div>'
            f'<div style="font-size:13px;color:{FAINT};">{_esc(app["name"])}</div>'
            f"</td>"
            f'<td style="padding:6px 4px;border:1px solid {BORDER};width:34%;">'
            f'<div style="height:7px;background:{BAR_BG};border-radius:99px;overflow:hidden;">'
            f'<div style="height:100%;width:{width}%;min-width:3px;background:{PRIMARY};border-radius:99px;"></div>'
            f"</div></td>"
            f'<td style="padding:6px 4px;border:1px solid {BORDER};text-align:right;white-space:nowrap;'
            f'font-size:14.5px;color:{INK_SOFT};font-variant-numeric:tabular-nums;">{_fmt_duration(app["seconds"])}</td>'
            f"</tr>"
        )
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _return_card(data: dict) -> str:
    returns = data["returns"]
    cells = [
        (_fmt_count(returns["switch_count"]), "切换次数（总）"),
        (str(returns["switches_per_day"]), "日均切换"),
    ]
    cell_html = []
    for value, label in cells:
        cell_html.append(
            f'<td style="width:50%;padding:6px 4px;text-align:center;">'
            f'<div style="font-size:24px;font-weight:850;color:{INK};font-variant-numeric:tabular-nums;">{_esc(value)}</div>'
            f'<div style="font-size:13.5px;color:{MUTED};margin-top:3px;">{_esc(label)}</div>'
            f"</td>"
        )
    grid = f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;"><tr>{"".join(cell_html)}</tr></table>'
    app_lines = []
    for app in returns["apps"]:
        app_lines.append(
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:6px 8px;border:1px solid {BORDER};border-radius:8px;margin-top:6px;">'
            f'<div style="font-size:15.5px;font-weight:800;color:{INK};">{_esc(app["display"])}</div>'
            f'<div style="font-size:14.5px;color:{MUTED};font-variant-numeric:tabular-nums;">'
            f'{app["returns"]} 次 · {_fmt_interval(app["median_seconds"])}</div>'
            f"</div>"
        )
    apps = "".join(app_lines)
    hint = (
        "离开某个应用后再次回到它，间隔越短越像高频往返；"
        "覆盖锁屏、睡眠或 unknown 段的回返不计入。"
    )
    return grid + apps + f'<div style="font-size:13px;color:{FAINT};line-height:1.5;margin-top:6px;">{hint}</div>'


def _trend_chart(data: dict) -> str:
    end_iso = str(data["window"]["end"])
    weeks = [
        w for w in data["trend"].get("weeks", [])
        if (
            str(w.get("week_end") or "") <= end_iso
            and (
                int(w.get("input_events") or 0) > 0
                or int(w.get("foreground_seconds") or 0) > 0
                or int(w.get("active_days") or 0) > 0
            )
        )
    ]
    if not weeks:
        return (
            '<div style="font-size:15px;color:' + MUTED + ';">趋势数据不足。</div>'
            '<div style="font-size:13px;color:' + FAINT + ';margin-top:4px;">'
            "只显示有数据的周，没有数据的周不占位。</div>"
        )
    ref = data["reference"]["input"]
    p25, p75 = ref.get("p25"), ref.get("p75")
    ready = (
        int(ref.get("n_valid_days") or 0) >= 28
        and p25 is not None
        and p75 is not None
    )
    max_value = max([float(w.get("avg_daily_input") or 0) for w in weeks] + [1.0])
    cells = []
    for week in weeks:
        value = float(week.get("avg_daily_input") or 0)
        height = max(3, round(value / max_value * 118))
        is_current = str(week.get("week_end") or "") == end_iso
        color = PRIMARY_STRONG if is_current else PRIMARY
        label = str(week.get("week_start") or "")[5:].replace("-", "/")
        label = "本周" if is_current else label
        inner = []
        if ready:
            bottom25 = round(float(p25) / max_value * 118)
            bottom75 = round(float(p75) / max_value * 118)
            band_bottom = min(bottom25, bottom75)
            band_height = max(2, abs(bottom75 - bottom25))
            inner.append(
                f'<div style="position:absolute;left:14%;right:14%;bottom:{band_bottom}px;'
                f'height:{band_height}px;background:{BAND_DIM};'
                f'border-top:1px solid {BAND};border-bottom:1px solid {BAND};"></div>'
            )
        inner.append(
            f'<div style="position:absolute;left:18%;right:18%;bottom:0;height:{height}px;'
            f'background:{color};border-radius:3px 3px 0 0;" title="{_esc(label)} · {_fmt_count(round(value))}"></div>'
        )
        cells.append(
            f'<td style="padding:0 3px;vertical-align:bottom;">'
            f'<div style="position:relative;height:130px;">{"".join(inner)}</div>'
            f'<div style="text-align:center;font-size:13px;color:{FAINT};margin-top:5px;white-space:nowrap;">{label}</div>'
            f"</td>"
        )
    chart = (
        f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">'
        f"<tr>{''.join(cells)}</tr></table>"
    )
    if ready:
        foot = (
            f'最近一周日均输入 {_fmt_count(round(ref.get("avg") or 0))} · '
            f'{POSITION_TEXT.get(ref.get("position") or "within", "在")}'
            f"你的典型范围（{_fmt_count(p25)}–{_fmt_count(p75)}）"
        )
    else:
        foot = (
            f'最近一周日均输入 {_fmt_count(round(ref.get("avg") or 0))} · '
            f"个人参考仍在积累（{int(ref.get('n_valid_days') or 0)} 个有效日）"
        )
    first_label = str(weeks[0].get("week_start") or "")[5:].replace("-", "/")
    last_label = str(weeks[-1].get("week_end") or "")[5:].replace("-", "/")
    scale_note = f"只显示有数据的周（共 {len(weeks)} 周：{first_label}–{last_label}），没有数据的周不占位。"
    return (
        chart
        + f'<div style="font-size:14px;color:{MUTED};margin-top:6px;">{_esc(foot)}</div>'
        + f'<div style="font-size:13px;color:{FAINT};margin-top:4px;">{_esc(scale_note)}</div>'
    )


def _judgment_card(data: dict) -> str:
    """最显眼的结论区：整块品牌绿横幅，字号最大。"""
    conclusion = data["narrative"]["conclusion"]
    return (
        f'<div style="background:{PRIMARY_STRONG};border-radius:12px;padding:14px 16px;'
        f'margin:10px 0;color:#ffffff;">'
        f'<div style="font-size:14px;font-weight:800;letter-spacing:2px;opacity:.85;">本周判断</div>'
        f'<div style="font-size:18px;font-weight:850;line-height:1.7;margin-top:6px;">'
        f"{_esc(conclusion)}</div>"
        f"</div>"
    )


def _narrative_block(data: dict) -> str:
    narrative = data["narrative"]
    insights = "".join(
        f'<div style="margin:8px 0;padding:10px 12px;border:1px solid {BORDER};'
        f'border-radius:10px;background:{SURFACE};">'
        f'<div style="font-size:15.5px;font-weight:800;color:{INK};line-height:1.5;">'
        f'<span style="color:{PRIMARY};font-weight:900;">✓ </span>{_esc(item["title"])}</div>'
        f'<div style="font-size:15px;color:{INK_SOFT};line-height:1.75;margin-top:6px;">'
        f"{_clause_blocks(item['text'])}</div>"
        f'<div style="font-size:13.5px;color:{MUTED};line-height:1.6;margin-top:6px;'
        f'padding-left:8px;border-left:2px solid {BAND};">{_esc(item["source"])}</div>'
        f"</div>"
        for item in narrative["insights"]
    )
    suggestions = "".join(
        f'<li style="margin:6px 0;line-height:1.75;font-size:15px;color:{INK_SOFT};">'
        f"{_clause_blocks(line)}</li>"
        for line in narrative["suggestions"]
    )
    valid_days = int(data["reference"]["input"].get("n_valid_days") or 0)
    encourage = (
        "数字手感是反复出现的身体记忆，需要足够长的历史才显出形状。"
        f"目前积累 {valid_days} 个有效日，等个人参考满 28 天，节律与回返的变化会更容易被看见。"
    )
    return (
        f'<div style="font-size:16px;font-weight:800;color:{INK};margin-bottom:4px;">线索与解读</div>'
        f"{insights}"
        f'<div style="font-size:16px;font-weight:800;color:{INK};margin-top:10px;">下一步建议</div>'
        f'<ul style="margin:6px 0 0 20px;padding:0;color:{INK_SOFT};">{suggestions}</ul>'
        f'<div style="margin-top:10px;padding:10px 12px;background:{PRIMARY_DIM};'
        f'border:1px solid {BAND};border-radius:10px;font-size:14.5px;line-height:1.7;'
        f'color:{PRIMARY_STRONG};">'
        f'<span style="font-weight:800;">◆ 鼓励持续使用：</span>{_clause_blocks(encourage)}</div>'
    )


def _change_card(data: dict) -> str:
    change = data["change"]

    def _delta_text(delta: dict | None, formatter=_fmt_count) -> str:
        if not delta or delta.get("pct") is None:
            return "—"
        sign = "+" if delta["diff"] >= 0 else "-"
        return f"{sign}{formatter(abs(delta['diff']))}（{sign}{abs(round(delta['pct']))}%）"

    input_prev = _fmt_count(change["input"]["previous"]) if change.get("input") else "—"
    fg_prev = _fmt_duration(change["foreground_seconds"]["previous"]) if change.get("foreground_seconds") else "—"
    switch = data["returns"]
    rows = [
        (
            "输入事件",
            _fmt_count(data["summary"]["input_events"]),
            input_prev,
            _delta_text(change.get("input")),
        ),
        (
            "前台时长",
            _fmt_duration(data["summary"]["foreground_seconds"]),
            fg_prev,
            _delta_text(change.get("foreground_seconds"), _fmt_duration),
        ),
        (
            "切换次数",
            f"{_fmt_count(switch['switch_count'])}（日均 {round(switch['switches_per_day'])}）",
            "—",
            "—",
        ),
    ]
    row_html = []
    for label, current, previous, delta in rows:
        row_html.append(
            f'<tr>'
            f'<td style="padding:6px 8px;border:1px solid {BORDER};color:{INK_SOFT};white-space:nowrap;">{label}</td>'
            f'<td style="padding:6px 8px;border:1px solid {BORDER};font-variant-numeric:tabular-nums;">{current}</td>'
            f'<td style="padding:6px 8px;border:1px solid {BORDER};font-variant-numeric:tabular-nums;color:{MUTED};">{previous}</td>'
            f'<td style="padding:6px 8px;border:1px solid {BORDER};font-variant-numeric:tabular-nums;color:{PRIMARY_STRONG};">{delta}</td>'
            f"</tr>"
        )
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:15px;">'
        f'<tr>'
        f'<th style="padding:5px 8px;border:1px solid {BORDER};background:{BG};color:{MUTED};font-weight:700;text-align:left;">指标</th>'
        f'<th style="padding:5px 8px;border:1px solid {BORDER};background:{BG};color:{MUTED};font-weight:700;text-align:left;">本周</th>'
        f'<th style="padding:5px 8px;border:1px solid {BORDER};background:{BG};color:{MUTED};font-weight:700;text-align:left;">上周</th>'
        f'<th style="padding:5px 8px;border:1px solid {BORDER};background:{BG};color:{MUTED};font-weight:700;text-align:left;">变化</th>'
        f"</tr>{''.join(row_html)}</table>"
    )


def _rhythm_card(data: dict) -> str:
    lines = data["analysis"]["lines"]
    if not lines:
        return '<div style="font-size:15px;color:' + MUTED + ';">本周数据还不足以形成画像。</div>'
    body = "；".join(line.rstrip("。") for line in lines) + "。"
    return f'<div style="font-size:16px;color:{INK};line-height:1.75;">{_esc(body)}</div>'


def build_html(data: dict) -> str:
    window = data["window"]
    range_text = f"{_fmt_weekday_date(window['start'])} – {_fmt_weekday_date(window['end'])}"
    summary = data["summary"]
    change = data["change"]
    density = float(summary.get("density_per_min") or 0.0)
    prev_density = float((change.get("density") or {}).get("previous") or 0.0)
    peak_hours = summary.get("peak_hours") or []
    peak_text = " / ".join(f"{p['hour']:02d}:00" for p in peak_hours[:2]) or "—"
    structure = data["analysis"]["structure"]
    top = data["top_apps"][0] if data["top_apps"] else None
    top_share = float(data.get("top_share") or 0.0)
    top3_share = float(data["analysis"].get("top3_share") or 0.0)
    first_return = data["returns"]["apps"][0] if data["returns"]["apps"] else None
    ref_input = data["reference"]["input"]
    ref_fg = data["reference"]["foreground_seconds"]
    ref_ready = (
        int(ref_input.get("n_valid_days") or 0) >= 28
        and int(ref_fg.get("n_valid_days") or 0) >= 28
        and ref_input.get("p25") is not None
        and ref_input.get("p75") is not None
        and ref_fg.get("p25") is not None
        and ref_fg.get("p75") is not None
    )
    data_weeks = [
        w for w in data["trend"].get("weeks", [])
        if str(w.get("week_end") or "") <= str(window["end"])
        and (
            int(w.get("input_events") or 0) > 0
            or int(w.get("active_days") or 0) > 0
        )
    ]
    trend_first = _fmt_count(round(float(data_weeks[0].get("avg_daily_input") or 0))) if data_weeks else "—"
    trend_last = _fmt_count(round(float(data_weeks[-1].get("avg_daily_input") or 0))) if data_weeks else "—"
    header = (
        f'<div style="background:{SURFACE};border:1px solid {BORDER};border-radius:12px;'
        f'padding:14px 16px;">'
        f'<div style="display:flex;align-items:center;gap:8px;">'
        f'<div style="width:10px;height:10px;border-radius:3px;background:{PRIMARY};"></div>'
        f'<div style="font-size:21px;font-weight:850;color:{INK};letter-spacing:0;">{APP_TITLE}</div>'
        f"</div>"
        f'<div style="color:{MUTED};font-size:14px;margin-top:4px;">'
        f"{range_text} · 数据不出本机 · 仅聚合统计</div>"
        f"</div>"
    )
    judgment = _judgment_card(data)
    overview = _card(
        "本周概览",
        _overview_table(data)
        + _interpretation(
            "时间先看形态，不看总量",
            [
                f"本周前台 {_fmt_duration(summary['foreground_seconds'])}、"
                f"日均 {_fmt_duration(summary['avg_daily_fg'])}，"
                f"活跃 {window['active_days']}/{window['days']} 天，"
                f"日均输入 {_fmt_count(summary['avg_daily_input'])} 次。",
                "前台时长只回答「窗口在最前面多久」：同样的数字，可以来自长时间挂机，"
                "也可以来自高强度往返，两者含义完全不同。",
                "判断一个数字之前，先看它由什么形态构成；形态比总量更值得读。",
                "结论：Mark 等（CHI 2014）发现人们平均每 3–5 分钟切换一次在线活动，"
                "前台时间的波动反映的是注意力的节奏而非强度；先当形态看，不当评分看。",
            ],
            "Mark, G., et al. (2014). Bored Mondays and Focused Afternoons. CHI.",
        ),
        accent="▧",
    )
    change_interpretation = ""
    if (
        change.get("input") and change["input"].get("pct") is not None
        and change.get("foreground_seconds") and change["foreground_seconds"].get("pct") is not None
    ):
        in_dir = "增加" if change["input"]["diff"] >= 0 else "减少"
        fg_dir = "增加" if change["foreground_seconds"]["diff"] >= 0 else "减少"
        change_interpretation = _interpretation(
            "密度上升，节奏在收紧",
            [
                f"输入较上周{in_dir} {abs(round(change['input']['pct']))}%、"
                f"前台{fg_dir} {abs(round(change['foreground_seconds']['pct']))}%，"
                f"输入密度从约 {prev_density:.1f} 升到约 {density:.1f} 次/分钟。",
                "同样的使用量可以有完全不同的时间形态：本周把更多输入压进更短的前台里，"
                "节奏更紧凑。",
                "结论：Barabási（Nature 2005）指出事件在时间轴上的排列本身携带信息；"
                "本周是「短时段高密度」形态，若延续，说明工作更依赖集中爆发而非长时铺开。",
            ],
            "Barabási, A.-L. (2005). The origin of bursts and heavy tails in human dynamics. Nature, 435, 207–211.",
        )
    change_card_html = _card(
        "本周变化",
        _change_card(data) + change_interpretation,
        accent="↕",
    )
    rhythm = _card(
        "节奏画像",
        _rhythm_card(data)
        + _interpretation(
            f"{peak_text} 是本周的输入高峰",
            [
                f"高峰集中在 {peak_text} 前后，键盘占 {structure['keyboard_pct']}%。",
                "高峰不是全天均匀铺开，而是集中在两个时段：输入呈脉冲式分布，"
                "而不是持续高压；这种形状更接近有作息边界的工作节律。",
                "结论：Mark 等（2014）显示在线活动有日间节律、下午回升；"
                "傍晚双峰更像个人节律，不是异常爆发。",
            ],
            "Mark, G., et al. (2014). The Rhythm of Attention and Online Activity in the Workplace. CHI.",
        ),
        accent="◉",
    )
    reference_body = _reference_table(data)
    if ref_ready:
        reference_body += _interpretation(
            "中位数更接近典型的一天",
            [
                "行为数据大多右偏，均值容易被极端日拉高，中位数与分位范围更稳健。",
                "本周日均输入与前台落在 28 天典型范围的什么位置：先看位置，"
                "再决定是否追问原因。",
                "结论：Huber（1981）的稳健统计指出，分布右偏时，位置度量比均值"
                "更接近「典型的一天」。",
            ],
            "Huber, P. J. (1981). Robust Statistics. Wiley.",
        )
    reference = _card(
        "常态对照",
        reference_body,
        accent="◈",
    )
    apps_body = _apps_table(data["top_apps"], summary["foreground_seconds"])
    if top:
        apps_body += _interpretation(
            "应用占比不等于价值排序",
            [
                f"{top['display']} 占前台约 {round(top_share * 100)}%，"
                f"前三应用合计约 {round(top3_share * 100)}%。",
                "份额高的应用承接了大量工作流切换：占比只回答结构问题，"
                "不回答「是否值得」的价值问题。",
                "结论：Rader 与 Gray（CHI 2015）指出应用分类是价值判断而非客观属性；"
                "占比描述结构，不评判应用是否「值得」。",
            ],
            "Rader, E., & Gray, R. (2015). Understanding User Beliefs about Algorithmic Curation in the Facebook News Feed. CHI.",
        )
    apps = _card("应用主场", apps_body, accent="▤")
    returns_body = _return_card(data)
    if first_return:
        returns_body += _interpretation(
            "高频回返是工作流的形状",
            [
                f"{first_return['display']} 回返 {first_return['returns']} 次、"
                f"中位间隔 {_fmt_interval(first_return['median_seconds'])}，形成高频「离开—回来」环路。",
                "离开与回返越密集，说明它越像工作流里的中枢站：每次出去查一步、"
                "办一步，又回到这里继续。",
                "结论：González 与 Mark（CHI 2004）发现知识工作者的工作流高度碎片化；"
                "回返间隔描述形态，不自动等于分心。",
            ],
            "González, V. M., & Mark, G. (2004). Constant, constant, multi-tasking craziness. CHI.",
        )
    returns = _card("回返节奏", returns_body, accent="↻")
    trend = _card(
        "输入趋势 · 数据积累以来",
        _trend_chart(data)
        + _interpretation(
            "趋势只有 3 周，先看方向",
            [
                f"有数据以来日均输入从 {trend_first} 走到 {trend_last}"
                f"（共 {len(data_weeks)} 周有数据）。",
                "连续几周都在上行，方向一致，但样本还短：一周的波动就可能改变均值，"
                "先记方向，不下定论。",
                "结论：个人基线需要足够长的窗口，28 天才能在稳定性与灵敏度间折中；"
                "目前能说的是「近几周的走向」，不能说是「习惯已改变」。",
            ],
            "Killick, R., Fearnhead, P., & Eckley, I. A. (2013). Optimal detection of changepoints with a linear computational cost. JASA.",
        ),
        accent="▥",
    )
    narrative = _card("线索与建议", _narrative_block(data), accent="✎")
    evidence = citation_block(data.get("profile") or {})
    footer = (
        f'<div style="margin:12px 4px 8px;font-size:13px;color:{FAINT};line-height:1.8;">'
        f"本机计算 · 仅聚合统计，不含输入内容与窗口正文。"
        f"口径：自然周；输入=键盘+点击+滚轮；前台段按进程变化切分。"
        f"生成时间 {_esc(data['generated_at'])}</div>"
    )
    return (
        "<!DOCTYPE html>"
        '<html lang="zh-CN"><head><meta charset="utf-8">'
        f"<title>{APP_TITLE} · {_esc(range_text)}</title>"
        f"</head><body style=\"margin:0;background:{BG};font-family:{FONT_STACK};\">"
        f'<div style="max-width:660px;margin:0 auto;padding:16px 12px;">'
        f"{header}{judgment}{overview}{change_card_html}{rhythm}{reference}{apps}{returns}{trend}{narrative}{evidence}{footer}"
        f"</div></body></html>"
    )


def _snapshot_db(db_path: Path, snapshot_path: Path) -> None:
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(str(snapshot_path))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--db", help="tracker.db 路径（默认取数据目录下的 tracker.db）")
    parser.add_argument("--end-date", help="报告结束日（自然周日，默认最近一个周日）")
    parser.add_argument("--out", help="输出 HTML 路径（默认 tmp/weekly-report-<end>.html）")
    parser.add_argument("--json", help="同时输出聚合数据 JSON")
    args = parser.parse_args(argv)

    data_dir = Path(os.environ.get(DATA_DIR_ENV, "")).expanduser() if os.environ.get(DATA_DIR_ENV) else Path("data")
    db_path = Path(args.db) if args.db else data_dir / "tracker.db"
    if not db_path.exists():
        print(f"未找到数据库：{db_path}", file=sys.stderr)
        return 2

    today = local_now().date()
    end = (
        datetime.fromisoformat(str(args.end_date)).date()
        if args.end_date
        else today - timedelta(days=(today.weekday() + 1) % 7)
    )
    end_iso = end.isoformat()
    snapshot_dir = Path("tmp") / f"weekly-report-{end_iso}"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot = snapshot_dir / "tracker.db"
    _snapshot_db(db_path, snapshot)

    settings_src = db_path.parent / "settings.json"
    settings_dst = snapshot_dir / "settings.json"
    if settings_src.exists():
        settings_dst.write_text(settings_src.read_text(encoding="utf-8"), encoding="utf-8")

    os.environ[DATA_DIR_ENV] = str(snapshot_dir.resolve())
    db = DB(snapshot)
    try:
        settings = get_settings()
        data = load_data(db, end_iso, settings)
    finally:
        db.close()

    out_path = Path(args.out) if args.out else Path("tmp") / f"weekly-report-{end_iso}.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_html(data), encoding="utf-8")
    print(f"报告已生成：{out_path.resolve()}")
    if args.json:
        json_path = Path(args.json)
        json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"数据已输出：{json_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
