"""AI-backed handfeel replay generation."""
import json
import os
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from interaction_rhythm.ai_config import load_ai_config, public_base_url


DEFAULT_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"
DEFAULT_MODEL = "mimo-v2.5-pro"

ALLOWED_CLAIM_IDS = {
    *(f"A{index:02d}" for index in range(1, 10)),
    *(f"Q{index:02d}" for index in range(1, 9)),
    *(f"U{index:02d}" for index in range(1, 4)),
}
FORBIDDEN_CLAIM_IDS = {f"F{index:02d}" for index in range(1, 13)}
FORBIDDEN_OUTPUT_TERMS = (
    "打了多少字",
    "写了多少内容",
    "人格",
    "确诊",
    "诊断为",
    "抑郁症",
    "焦虑症",
    "多动症",
    "超过用户",
    "更了解你的意图",
    "系统比用户更了解",
    "全天数据完整",
    "完全私密",
    "绝不上传",
)
FORBIDDEN_OUTPUT_PATTERNS = (
    re.compile(r"超过(?:了)?\s*\d+(?:\.\d+)?\s*%?\s*(?:的)?\s*用户", re.IGNORECASE),
    re.compile(r"超过(?:了)?\s*其他\s*用户", re.IGNORECASE),
)

# ── 文本净化 ──────────────────────────────────────────────
TEXT_REPLACEMENTS = (
    # 效率 → 观察
    ("以提升效率", "方便回看现场"),
    ("提升效率", "更容易回看现场"),
    ("提升生产力", "响应更密"),
    ("生产力", "响应强度"),
    ("时间管理", "时间分布"),
    ("时间分配", "时间分布"),
    ("考虑优化", "可以回看"),
    ("优化", "回看"),
    ("专注于", "回看"),
    ("高效", "密集"),
    ("低效", "稀疏"),
    ("效率很高", "响应很密"),
    ("效率很低", "响应很稀"),
    ("高效工作", "密集响应"),
    ("深度工作", "连续响应"),
    ("心流状态", "连续响应"),
    ("心流", "连续响应"),
    ("沉浸", "连续"),
    # 任务 → 时段
    ("工作任务", "响应时段"),
    ("浏览任务", "浏览时段"),
    ("完成任务", "响应"),
    ("完成工作", "响应"),
    ("任务切换", "应用切换"),
    ("多任务", "多应用切换"),
    ("单任务", "单应用"),
    ("工作节奏", "响应节奏"),
    ("工作状态", "响应状态"),
    ("工作模式", "响应模式"),
    # 键盘表述
    ("键盘敲击", "键盘响应"),
    ("高敲击", "高键盘响应"),
    ("敲击冲刺", "键盘冲刺"),
    ("连续敲击", "连续键盘"),
    ("敲击时段", "键盘时段"),
    ("敲击", "键盘"),
    ("打字速度", "键盘密度"),
    ("打字", "键盘"),
    ("输入速度", "键盘密度"),
    # 代码/文件推测
    ("点开相关代码文件", "回看那一段应用构成"),
    ("相关代码文件", "那一段应用构成"),
    ("代码文件", "应用构成"),
    ("编写代码", "连续键盘"),
    ("写代码", "连续键盘"),
    ("coding", "连续键盘"),
    ("编程", "连续键盘"),
    ("debug", "连续键盘"),
    ("调试", "连续键盘"),
    ("写文档", "连续键盘"),
    ("写文章", "连续键盘"),
    ("写作", "连续键盘"),
    ("阅读文档", "浏览时段"),
    ("浏览网页", "浏览时段"),
    ("看视频", "浏览时段"),
    ("开会", "浏览时段"),
    ("会议", "浏览时段"),
    ("聊天", "浏览时段"),
    # 评判性词汇
    ("浪费时间", "低响应时段"),
    ("浪费", "低响应"),
    ("拖延", "间隔"),
    ("分心", "切换"),
    ("分神", "切换"),
    ("走神", "切换"),
    ("懒惰", "轻量"),
    ("懈怠", "轻量"),
    ("摸鱼", "轻量"),
    ("划水", "轻量"),
    ("偷懒", "轻量"),
    ("无聊", "轻量"),
    ("疲惫", "轻量"),
    ("疲劳", "轻量"),
    ("累", "轻量"),
    ("困", "轻量"),
    # 建议性词汇
    ("你应该", "可以"),
    ("你需要", "可以"),
    ("建议你", "可以"),
    ("建议", "观察"),
    ("应该", "可以"),
    ("需要", "可以"),
    ("必须", "可以"),
    ("不妨", "可以"),
    ("最好", "可以"),
    ("尝试", "回看"),
    ("试试", "回看"),
    ("改进", "回看"),
    ("改善", "回看"),
    ("提升", "变化"),
    ("提高", "变化"),
    ("增强", "变化"),
    ("减少", "变化"),
    ("降低", "变化"),
    ("避免", "观察"),
    ("注意", "观察"),
    ("关注", "观察"),
    ("重视", "观察"),
    # 推测性词汇
    ("你可能在", "数据显示"),
    ("你似乎在", "数据显示"),
    ("你好像在", "数据显示"),
    ("你在", "数据显示"),
    ("可能", "数据显示"),
    ("也许", "数据显示"),
    ("大概", "数据显示"),
    ("似乎", "数据显示"),
    ("好像", "数据显示"),
    ("感觉", "数据显示"),
    ("猜测", "数据显示"),
    ("推测", "数据显示"),
    ("估计", "数据显示"),
    ("判断", "数据显示"),
    ("认为", "数据显示"),
    ("觉得", "数据显示"),
    # 目标/计划词汇
    ("目标", "方向"),
    ("计划", "记录"),
    ("规划", "记录"),
    ("安排", "分布"),
    ("优先级", "顺序"),
    ("习惯", "模式"),
    ("养成习惯", "形成模式"),
    ("自律", "规律"),
    ("坚持", "持续"),
    ("打卡", "记录"),
    ("完成率", "响应率"),
    # 过度修饰
    ("非常", ""),
    ("十分", ""),
    ("特别", ""),
    ("极其", ""),
    ("超级", ""),
    ("超级棒", ""),
    ("很棒", ""),
    ("很好", ""),
    ("太好了", ""),
    ("厉害", ""),
    ("优秀", ""),
    ("出色", ""),
    ("完美", ""),
    ("糟糕", ""),
    ("很差", ""),
    ("太差", ""),
    ("很烂", ""),
)


def _clean_url(base_url: str) -> str:
    base = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _headers(api_key: str, header_mode: str = "") -> dict:
    header_mode = (header_mode or os.getenv("MIMO_AUTH_HEADER", "")).strip().lower()
    headers = {"Content-Type": "application/json"}
    if header_mode == "api-key":
        headers["api-key"] = api_key
    elif header_mode == "authorization":
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["api-key"] = api_key
    return headers


def _env_first(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _provider_name(provider: str, model: str = "") -> str:
    names = {
        "deepseek": "DeepSeek",
        "openai": "OpenAI 兼容模型",
        "mimo": "MiMo",
    }
    return names.get(provider, provider or "AI")


def _provider_note(provider: str) -> str:
    return f"由{_provider_name(provider)}根据本机聚合活动数据生成，不包含输入内容或窗口正文。"


def _app_name(db, app: str) -> str:
    try:
        return db._app_display_name(app)
    except Exception:
        name = (app or "").strip()
        return name[:-4] if name.lower().endswith(".exe") else name


def _hour_range(hour: int | None) -> str:
    if hour is None:
        return ""
    return f"{hour:02d}:00-{(hour + 1) % 24:02d}:00"


def _segment_density(responses: int, active_seconds: int) -> str:
    """判断一个时段的响应密度等级"""
    if responses == 0:
        return "静默"
    if active_seconds <= 0:
        return "覆盖不足"
    rpm = responses / (active_seconds / 60)
    if rpm >= 8:
        return "密集"
    if rpm >= 3:
        return "稳定"
    return "零星"


def _build_timeline_segments(db, date: str) -> list[dict]:
    """把一天切成6个4小时段，给AI时序感"""
    segments = []
    for start in range(0, 24, 4):
        total_r = 0
        top_app = ""
        top_r = 0
        for h in range(start, start + 4):
            detail = db.hour_detail(date, h)
            total_r += detail.get("responses", 0)
            for app in detail.get("apps", []):
                if app.get("responses", 0) > top_r:
                    top_r = app["responses"]
                    top_app = _app_name(db, app.get("app", ""))
        active_s = sum(
            int((db.hour_detail(date, h).get("coverage") or {}).get("healthy_seconds") or 0)
            for h in range(start, start + 4)
        )
        segments.append({
            "range": f"{start:02d}:00-{(start + 4) % 24:02d}:00",
            "responses": total_r,
            "density": _segment_density(total_r, active_s),
            "top_app": top_app if total_r > 0 else "",
        })
    return segments


def _median(values: list[int]) -> float:
    """计算中位数，空列表返回0"""
    if not values:
        return 0
    return statistics.median(values)


def _active_minutes(db, date: str) -> int:
    """统计有键盘/鼠标响应的分钟数（排除纯前台停留无操作的时间）"""
    try:
        rows = db._conn.execute(
            "SELECT DISTINCT substr(ts, 1, 16) FROM app_interactions "
            "WHERE substr(ts, 1, 10) = ? AND (keyboard > 0 OR mouse > 0)",
            (date,),
        ).fetchall()
        return len(rows)
    except Exception:
        return 0


def _build_7d_comparison(db, date: str) -> dict:
    """对比今天和过去7天中位数（按有响应的分钟归一化）"""
    try:
        today = db.day_stats(date)
        today_r = int(today.get("keystrokes", 0)) + int(today.get("mouse_events", 0))
    except Exception:
        return {}

    today_mins = _active_minutes(db, date)
    if today_mins < 5:
        return {}

    past_rpm = []
    past_responses = []
    d = datetime.strptime(date, "%Y-%m-%d")
    for i in range(1, 8):
        prev = (d - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            s = db.day_stats(prev)
            r = int(s.get("keystrokes", 0)) + int(s.get("mouse_events", 0))
            mins = _active_minutes(db, prev)
            if r > 0 and mins >= 5:
                past_responses.append(r)
                past_rpm.append(round(r / mins, 2))
        except Exception:
            pass

    if not past_rpm:
        return {}

    today_rpm = round(today_r / today_mins, 2)
    median_rpm = _median(past_rpm)
    diff_pct = round((today_rpm - median_rpm) / max(0.01, median_rpm) * 100)

    if len(past_rpm) >= 3:
        sorted_rpm = sorted(past_rpm)
        q1 = sorted_rpm[len(sorted_rpm) // 4]
        q3 = sorted_rpm[len(sorted_rpm) * 3 // 4]
        iqr = q3 - q1
        volatility = "波动大" if iqr > median_rpm * 0.5 else "波动小"
    else:
        volatility = "数据少"

    return {
        "today_responses": today_r,
        "today_rpm": today_rpm,
        "today_active_mins": today_mins,
        "median_7d_rpm": round(median_rpm, 2),
        "diff_percent": diff_pct,
        "trend": "比平时更密" if diff_pct > 15 else ("比平时更疏" if diff_pct < -15 else "和平时持平"),
        "volatility": volatility,
        "sample_days": len(past_rpm),
    }


def _compact_day_context(db, date: str) -> dict:
    """组装给AI的完整上下文：当天数据 + 时间分段 + 7天对比"""
    stats = db.day_stats(date)
    apps = db.app_chart(date)
    hourly = db.hourly_chart(date)
    keyboard = int(stats.get("keystrokes") or 0)
    mouse = int(stats.get("mouse_events") or 0)
    split_available = bool(stats.get("mouse_split_available"))
    mouse_buttons = int(stats.get("mouse_button_events") or 0)
    scroll_events = int(stats.get("scroll_events") or 0)
    responses = keyboard + mouse
    peak_hour = max(range(24), key=lambda h: hourly[h]) if responses else None
    top_hours = sorted(
        ({"hour": h, "range": _hour_range(h), "responses": int(v)} for h, v in enumerate(hourly) if v),
        key=lambda item: item["responses"],
        reverse=True,
    )[:5]
    top_apps = []
    for app in apps[:6]:
        top_apps.append({
            "name": _app_name(db, app.get("app", "")),
            "minutes": round(int(app.get("seconds") or 0) / 60),
            "responses": int(app.get("responses") or 0),
            "share": round((app.get("response_share") or 0) * 100),
            "density": app.get("response_density") or 0,
        })

    segments = _build_timeline_segments(db, date)
    comparison = _build_7d_comparison(db, date)

    kb_ratio = round(keyboard / max(1, responses) * 100)
    mouse_ratio = 100 - kb_ratio
    if kb_ratio >= 70:
        input_type = "键盘主导"
    elif mouse_ratio >= 70:
        input_type = "鼠标主导"
    else:
        input_type = "混合输入"

    metric_version = stats.get("metric_version") or "legacy-1.x"
    coverage = stats.get("coverage") or {"status": "insufficient_data", "ratio": None}
    facts = [
        {
            "fact_id": f"{date}:keyboard",
            "metric_id": "IR-EVT-001",
            "metric_version": metric_version,
            "value": keyboard,
            "unit": "event",
        },
        {
            "fact_id": f"{date}:foreground",
            "metric_id": "IR-FG-001",
            "metric_version": metric_version,
            "value": int(stats.get("foreground_seconds") or stats.get("total_seconds") or 0),
            "unit": "second",
        },
    ]
    if split_available:
        facts.extend([
            {
                "fact_id": f"{date}:mouse-buttons",
                "metric_id": "IR-EVT-002",
                "metric_version": metric_version,
                "value": mouse_buttons,
                "unit": "event",
            },
            {
                "fact_id": f"{date}:scroll",
                "metric_id": "IR-EVT-003",
                "metric_version": metric_version,
                "value": scroll_events,
                "unit": "event",
            },
        ])
    else:
        facts.append({
            "fact_id": f"{date}:mouse-legacy",
            "metric_id": "IR-EVT-004",
            "metric_version": metric_version,
            "value": mouse,
            "unit": "event",
            "boundary": "legacy mouse count cannot be split into clicks and scrolls",
        })
    facts.extend([
        {
            "fact_id": f"{date}:hourly-distribution",
            "metric_id": "IR-DER-004",
            "metric_version": metric_version,
            "value": top_hours,
            "unit": "event_by_local_hour",
        },
        {
            "fact_id": f"{date}:coverage",
            "metric_id": "IR-HLT-003",
            "metric_version": metric_version,
            "value": coverage,
            "unit": "ratio",
        },
    ])

    return {
        "date": date,
        "keyboard": keyboard,
        "mouse": mouse,
        "mouse_buttons": mouse_buttons,
        "scroll_events": scroll_events,
        "mouse_split_available": split_available,
        "responses": responses,
        "input_type": input_type,
        "kb_ratio": kb_ratio,
        "foreground_minutes": round(int(stats.get("foreground_seconds") or stats.get("total_seconds") or 0) / 60),
        "peak_hour": peak_hour,
        "peak_range": _hour_range(peak_hour),
        "top_hours": top_hours,
        "top_apps": top_apps,
        "segments": segments,
        "comparison": comparison,
        "metric_version": metric_version,
        "coverage": coverage,
        "facts": facts,
    }


def _extract_json(text: str) -> dict | None:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


CLAIM_METRIC_REQUIREMENTS = {
    "A01": {"IR-EVT-001"},
    "A02": {"IR-EVT-002", "IR-EVT-003"},
    "A03": {"IR-FG-001"},
    "A06": {"IR-HLT-003"},
    "A09": {"IR-HLT-003"},
    "Q02": {"IR-DER-004"},
    "Q08": {"IR-HLT-003", "IR-BAS-001"},
}


def _contains_forbidden_output(text: str) -> bool:
    lowered = str(text or "").casefold()
    if any(term.casefold() in lowered for term in FORBIDDEN_OUTPUT_TERMS):
        return True
    return any(pattern.search(str(text or "")) for pattern in FORBIDDEN_OUTPUT_PATTERNS)


def _validated_response_items(items, context: dict) -> list[dict]:
    """Keep usable model items and discard only the malformed or unsafe item."""
    facts = {
        str(fact.get("fact_id")): fact
        for fact in context.get("facts", [])
        if isinstance(fact, dict) and fact.get("fact_id")
    }
    theories = {
        str(item.get("theory_id")): item
        for item in context.get("research_context", [])
        if isinstance(item, dict) and item.get("theory_id")
    }
    metric_version = str(context.get("metric_version") or "")
    validated = []
    for candidate in items if isinstance(items, list) else []:
        item = candidate if isinstance(candidate, dict) else {"text": candidate}
        raw_text = _clean_text(str(item.get("text") or item.get("finding") or ""))
        if not raw_text or _contains_forbidden_output(raw_text):
            continue

        supplied_fact_ids = item.get("fact_ids") if isinstance(item.get("fact_ids"), list) else []
        fact_ids = [
            str(value).strip()
            for value in supplied_fact_ids
            if str(value).strip() in facts
        ]
        metric_ids = sorted({
            str(facts[fact_id].get("metric_id") or "")
            for fact_id in fact_ids
            if str(facts[fact_id].get("metric_id") or "")
        })
        theory_ids = [
            str(value).strip()
            for value in item.get("theory_ids", [])
            if str(value).strip() in theories
        ] if isinstance(item.get("theory_ids"), list) else []
        claim_id = str(item.get("claim_id") or "INTERPRETATION").strip().upper()
        if claim_id in FORBIDDEN_CLAIM_IDS:
            continue
        confidence = str(item.get("confidence") or "medium").strip().lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "medium"
        validated.append({
            "text": raw_text,
            "fact_ids": fact_ids,
            "metric_ids": metric_ids,
            "theory_ids": theory_ids,
            "claim_id": claim_id,
            "metric_version": metric_version,
            "boundary": _clean_text(str(item.get("boundary") or "")),
            "confidence": confidence,
        })
    return validated[:8]


def _validate_structured_sentences(text: str, context: dict) -> tuple[list[dict] | None, str]:
    """Compatibility parser for old and new model outputs with partial recovery."""
    parsed = _extract_json(text)
    if isinstance(parsed, dict):
        items = parsed.get("findings")
        if not isinstance(items, list):
            items = parsed.get("sentences")
        if not isinstance(items, list):
            items = parsed.get("insights")
        validated = _validated_response_items(items, context)
        if validated:
            return validated, ""
        if isinstance(items, list):
            return None, "no_usable_model_content"
    conclusion, findings = _parse_natural_output(text)
    natural_items = [conclusion, *findings] if conclusion else []
    validated = _validated_response_items(natural_items, context)
    if validated:
        return validated, ""
    return None, "no_usable_model_content"


def _factual_replay(context: dict, reason: str = "") -> dict:
    """生成带完整来源的本地事实回放，作为 AI 不可用时的唯一降级路径。"""
    facts = {
        str(fact["fact_id"]): fact
        for fact in context.get("facts", [])
        if isinstance(fact, dict) and fact.get("fact_id")
    }
    metric_version = str(context.get("metric_version") or "")

    def sentence(text: str, fact_ids: list[str], claim_id: str, boundary: str) -> dict:
        metric_ids = []
        for fact_id in fact_ids:
            metric_id = str(facts[fact_id].get("metric_id") or "")
            if metric_id and metric_id not in metric_ids:
                metric_ids.append(metric_id)
        return {
            "text": text,
            "fact_ids": fact_ids,
            "metric_ids": metric_ids,
            "claim_id": claim_id,
            "metric_version": metric_version,
            "boundary": boundary,
        }

    sentences = []
    keyboard_id = f"{context['date']}:keyboard"
    sentences.append(sentence(
        f"今天记录到 {context['keyboard']} 次键盘事件。",
        [keyboard_id],
        "A01",
        "只描述被捕获的键盘事件，不代表字数或输入内容。",
    ))
    if context["mouse_split_available"]:
        sentences.append(sentence(
            f"今天记录到 {context['mouse_buttons']} 次鼠标点击和 {context['scroll_events']} 次滚轮事件。",
            [f"{context['date']}:mouse-buttons", f"{context['date']}:scroll"],
            "A02",
            "只描述点击与滚轮计数，不包含坐标或轨迹。",
        ))
    else:
        sentences.append(sentence(
            f"今天记录到 {context['mouse']} 次鼠标事件，旧口径无法拆分点击与滚轮。",
            [f"{context['date']}:mouse-legacy"],
            "A08",
            "历史鼠标事实无法拆分为点击与滚轮。",
        ))

    foreground_minutes = int(context.get("foreground_minutes") or 0)
    if foreground_minutes:
        sentences.append(sentence(
            f"今天前台共 {foreground_minutes} 分钟。",
            [f"{context['date']}:foreground"],
            "A03",
            "前台时长不等于实际使用、注意或工作时长。",
        ))

    top_hours = context.get("top_hours") or []
    if top_hours:
        peak_range = str(top_hours[0].get("range") or "")
        sentences.append(sentence(
            f"输入事件更集中在 {peak_range}。",
            [f"{context['date']}:hourly-distribution"],
            "Q02",
            "只描述事件的时间分布，不解释当时原因。",
        ))

    coverage = context.get("coverage") or {}
    if coverage.get("ratio") is not None:
        ratio = round(float(coverage.get("ratio") or 0) * 100)
        healthy = int(coverage.get("healthy_seconds") or 0)
        expected = int(coverage.get("expected_seconds") or 0)
        sentences.append(sentence(
            f"健康覆盖为 {ratio}%（{healthy}/{expected} 秒）。",
            [f"{context['date']}:coverage"],
            "A06",
            "覆盖只描述采集健康，不代表全天完整记录。",
        ))

    findings = [item["text"] for item in sentences[1:4]]
    return {
        "ok": True,
        "fallback": True,
        "fallback_reason": reason or "provider_unavailable",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "assistant": "本地事实回放",
        "model": "deterministic",
        "model_note": "模型未就绪或输出未通过来源合同，已改用本地规则回放；不调用模型，记录不会离开本机。",
        "provider": "rules",
        "interface": "POST /api/replay/jobs + GET /api/replay/jobs?id=...",
        "scope": "day",
        "date": context["date"],
        "days": 1,
        "conclusion": sentences[0]["text"],
        "summary": context,
        "findings": findings,
        "judgments": findings,
        "suggestions": [],
        "provenance": {
            "metric_version": metric_version,
            "facts": context.get("facts", []),
            "fact_ids": [fact["fact_id"] for fact in context.get("facts", [])],
            "metric_ids": sorted({fact.get("metric_id") for fact in context.get("facts", [])}),
            "claim_id": sentences[0]["claim_id"],
            "boundary": "descriptive_personal_recall_only",
        },
        "sentences": sentences,
        "raw_text": "",
    }


def _research_context() -> list[dict]:
    """Return the reviewed local knowledge cards available to the model."""
    from interaction_rhythm.theory_catalog import REVIEWED_THEORY_CARDS

    return [card.as_public_dict() for card in REVIEWED_THEORY_CARDS]


def _ai_context_payload(context: dict, include_research: bool = True) -> dict:
    """构造唯一的 AI 出站数据合同；预览和实际 prompt 必须共用它。"""
    payload = {
        "date": context.get("date"),
        "metric_version": context.get("metric_version"),
        "keyboard": int(context.get("keyboard") or 0),
        "mouse": int(context.get("mouse") or 0),
        "mouse_buttons": int(context.get("mouse_buttons") or 0),
        "scroll_events": int(context.get("scroll_events") or 0),
        "mouse_split_available": bool(context.get("mouse_split_available")),
        "responses": int(context.get("responses") or 0),
        "input_type": context.get("input_type") or "",
        "keyboard_ratio_percent": int(context.get("kb_ratio") or 0),
        "foreground_minutes": int(context.get("foreground_minutes") or 0),
        "peak_hour": context.get("peak_hour"),
        "peak_range": context.get("peak_range") or "",
        "coverage": context.get("coverage") or {},
        "hourly_distribution": context.get("top_hours") or [],
        "application_aggregates": context.get("top_apps") or [],
        "foreground_segments": context.get("segments") or [],
        "personal_comparison": context.get("comparison") or {},
        "facts": context.get("facts") or [],
    }
    if include_research:
        payload["research_context"] = context.get("research_context") or _research_context()
    return payload


DEFAULT_AGENT_PROMPT = (
    "你是个人交互节律分析助手。请结合当天聚合数据、七日个人比较、应用分布、"
    "时序片段和研究卡片，给出有解释力的中文分析。"
    "先说观察到的数据形状，再提出谨慎的可能解释，并给出能帮助本人回想的"
    "问题和一两个低成本行动。允许讨论节律、连续性、切换、密度、专注、疲劳、"
    "效率和可能的工作状态，但解释必须使用“可能”“看起来”“值得回想”等概率措辞。"
    "研究卡片是解释视角，不是对个人状态的因果证明；只引用真正使用过的 theory_id。"
    "不得虚构输入内容、窗口内容或具体任务，不得做医学或人格诊断，不得与其他用户排名，"
    "不得声称采集全天完整或绝对私密。不要输出内部推理过程。"
    "只输出 JSON 对象：conclusion 为一句核心判断；findings 为 3-5 个对象，"
    "每个对象含 text、fact_ids、theory_ids、confidence；reflection_questions 为 1-2 个回想问题，"
    "用温和开放的口吻引导本人回忆（如以“想起”“当时”“是不是”开头），问题要紧扣数据中具体出现的时间段、应用或密度变化，不假设具体工作内容，不用质问语气；"
    "suggestions 为 1-2 个行动；references 为使用过的 theory_id。"
    "fact_ids 和 theory_ids 只能从用户数据中选择。"
)


def agent_prompt_override() -> str:
    """返回用户在设置页自定义的 Agent 提示词；未设置时返回空字符串。"""
    try:
        from settings import get_settings

        value = str(get_settings().get("ai_agent_prompt") or "").strip()
        return value or ""
    except Exception:
        return ""


def current_agent_prompt() -> str:
    """返回当前生效的 Agent 提示词（自定义优先，否则内置默认）。"""
    return agent_prompt_override() or DEFAULT_AGENT_PROMPT


def _build_ai_messages(context: dict) -> list[dict]:
    """Build the production prompt for an evidence-rich daily interpretation."""
    system = current_agent_prompt()
    user = (
        "分析以下本地聚合记录。让结论具体、可回想，避免空泛套话：\n"
        + json.dumps(_ai_context_payload(context), ensure_ascii=False)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def ai_context_preview(db, date: str | None = None) -> dict:
    """返回不含密钥的 provider 状态与实际聚合出站字段预览。"""
    context = _compact_day_context(db, date or datetime.now().strftime("%Y-%m-%d"))
    config = load_ai_config()
    payload = _ai_context_payload(context)
    return {
        "ok": True,
        "date": context["date"],
        "provider": config.provider,
        "base_url": public_base_url(config.base_url),
        "model": config.model,
        "cloud_enabled": bool(config.cloud_enabled),
        "key_configured": bool(config.api_key),
        "fields": [
            "date",
            "metric_version",
            "keyboard",
            "mouse",
            "mouse_buttons",
            "scroll_events",
            "mouse_split_available",
            "responses",
            "input_type",
            "keyboard_ratio_percent",
            "foreground_minutes",
            "peak_hour",
            "peak_range",
            "coverage",
            "hourly_distribution",
            "application_aggregates",
            "foreground_segments",
            "personal_comparison",
            "facts",
            "research_context",
        ],
        "excluded_fields": [
            "key_values",
            "input_text",
            "mouse_coordinates",
            "screenshots",
            "window_titles",
            "browser_page_content",
        ],
        "payload": payload,
    }


def _normalize_list(value) -> list[str]:
    if isinstance(value, list):
        return [_clean_text(str(item).strip()) for item in value if str(item).strip()]
    if isinstance(value, str):
        lines = [line.strip(" -\t0123456789.、") for line in value.splitlines()]
        return [_clean_text(line) for line in lines if line]
    return []


def _clean_text(text: str) -> str:
    cleaned = str(text or "")
    cleaned = cleaned.strip().strip('"').strip("'")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _safe_text_list(value, limit: int) -> list[str]:
    values = value if isinstance(value, list) else ([value] if isinstance(value, str) else [])
    result = []
    for item in values:
        text = _clean_text(str(item.get("text") if isinstance(item, dict) else item or ""))
        if text and not _contains_forbidden_output(text) and text not in result:
            result.append(text)
    return result[:limit]


def _parse_ai_response(text: str, context: dict) -> tuple[dict | None, str]:
    """Parse the richer response contract while accepting older or natural output."""
    parsed = _extract_json(text)
    if not isinstance(parsed, dict):
        conclusion, findings = _parse_natural_output(text)
        if not conclusion or _contains_forbidden_output(conclusion):
            return None, "no_usable_model_content"
        return {
            "conclusion": conclusion,
            "insights": _validated_response_items(findings, context),
            "reflection_questions": [],
            "suggestions": [],
            "references": [],
            "confidence": "medium",
        }, ""

    candidates = parsed.get("findings")
    if not isinstance(candidates, list):
        candidates = parsed.get("insights")
    if not isinstance(candidates, list):
        candidates = parsed.get("sentences")
    insights = _validated_response_items(candidates, context)

    conclusion = _clean_text(str(parsed.get("conclusion") or parsed.get("summary") or ""))
    if conclusion and _contains_forbidden_output(conclusion):
        conclusion = ""
    if not conclusion and insights:
        conclusion = insights[0]["text"]
    if not conclusion:
        return None, "no_usable_model_content"

    research = {
        str(item.get("theory_id")): item
        for item in context.get("research_context", [])
        if isinstance(item, dict) and item.get("theory_id")
    }
    reference_ids = []
    raw_references = parsed.get("references") if isinstance(parsed.get("references"), list) else []
    for value in raw_references:
        theory_id = str(value.get("theory_id") if isinstance(value, dict) else value or "").strip()
        if theory_id in research and theory_id not in reference_ids:
            reference_ids.append(theory_id)
    for item in insights:
        for theory_id in item.get("theory_ids", []):
            if theory_id in research and theory_id not in reference_ids:
                reference_ids.append(theory_id)

    confidence = str(parsed.get("confidence") or "medium").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    return {
        "conclusion": conclusion,
        "insights": insights[:5],
        "reflection_questions": _safe_text_list(
            parsed.get("reflection_questions") or parsed.get("questions"), 2
        ),
        "suggestions": _safe_text_list(parsed.get("suggestions") or parsed.get("actions"), 2),
        "references": [research[theory_id] for theory_id in reference_ids[:3]],
        "confidence": confidence,
    }, ""


def _parse_natural_output(text: str) -> tuple[str, list[str]]:
    """解析自然语言输出：第一句=结论，其余=线索"""
    cleaned = _clean_text(text or "")
    if not cleaned:
        return "", []

    # 按句号/换行拆分
    sentences = re.split(r'[。\n]+', cleaned)
    sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 4]

    if not sentences:
        return "", []

    conclusion = sentences[0]
    findings = sentences[1:4]  # 最多3条线索

    return conclusion, findings


def _fallback_parse(text: str) -> tuple[str, list[str]]:
    """解析自然语言输出：按行拆分"""
    lines = [line.strip(" -\t0123456789.、") for line in (text or "").splitlines()]
    lines = [_clean_text(line) for line in lines if line and len(line) > 4]
    if not lines:
        return "今天的数据形状还比较轻。", []
    conclusion = lines[0]
    findings = lines[1:4]
    return conclusion, findings


def _chat_text_from_response(data: dict) -> str:
    try:
        message = data["choices"][0]["message"]
        return str(message.get("content") or message.get("reasoning_content") or "").strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        pass
    message = data.get("message") if isinstance(data, dict) else None
    if isinstance(message, dict):
        content = str(message.get("content") or "").strip()
        if content:
            return content
        thinking = str(message.get("thinking") or "").strip()
        if thinking:
            cleaned = re.sub(r"^\s*(嗯|好|首先)[，,。]?.*?(?=\n\n|。)", "", thinking, flags=re.S).strip()
            return cleaned or thinking
    return ""

def _call_openai_compatible_detailed(
    messages: list[dict],
    provider: str,
    api_key: str,
    base_url: str,
    model: str,
    header_mode: str = "authorization",
    timeout: int | None = None,
    thinking_enabled: bool = False,
) -> dict:
    if timeout is None:
        timeout = load_ai_config().timeout
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": 0.35,
        "max_tokens": 1200,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    if provider == "deepseek":
        if thinking_enabled:
            payload["enable_thinking"] = True
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        _clean_url(base_url),
        data=body,
        headers=_headers(api_key, header_mode),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{_provider_name(provider)} API {exc.code}: {error_body[:240]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{_provider_name(provider)}连接失败：{exc.reason}") from exc

    data = json.loads(raw)
    text = _chat_text_from_response(data)
    if not text:
        raise RuntimeError(f"{_provider_name(provider)}返回为空")
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        usage = {}
    return {
        "text": text,
        "usage": {
            key: int(value)
            for key, value in usage.items()
            if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool)
        },
        "http_status": int(getattr(response, "status", 200)),
    }


def _call_openai_compatible(
    messages: list[dict],
    provider: str,
    api_key: str,
    base_url: str,
    model: str,
    header_mode: str = "authorization",
    timeout: int | None = None,
    thinking_enabled: bool = False,
) -> str:
    return _call_openai_compatible_detailed(
        messages,
        provider,
        api_key,
        base_url,
        model,
        header_mode,
        timeout,
        thinking_enabled,
    )["text"]


def _call_mimo(messages: list[dict]) -> str:
    api_key = os.getenv("MIMO_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未配置 MIMO_API_KEY")
    model = os.getenv("MIMO_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    return _call_openai_compatible(
        messages,
        "mimo",
        api_key,
        os.getenv("MIMO_BASE_URL", DEFAULT_BASE_URL),
        model,
        os.getenv("MIMO_AUTH_HEADER", ""),
    )


def _select_ai_provider() -> tuple[str, str, str]:
    config = load_ai_config()
    return config.provider, config.base_url, config.model


def _call_ai(
    messages: list[dict],
    timeout: int | None = None,
    thinking_enabled: bool | None = None,
) -> tuple[str, str, str]:
    config = load_ai_config()
    if thinking_enabled is None:
        thinking_enabled = bool(config.thinking_enabled)
    provider, base_url, model = config.provider, config.base_url, config.model
    if not config.cloud_enabled or not config.api_key:
        raise RuntimeError(
            "未配置 AI API Key：请在设置中填写所选服务的 API Key，"
            "或导出数据后交给外部 AI 分析。"
        )
    if not base_url or not model:
        raise RuntimeError("云端 AI 缺少 base URL 或 model")
    return (
        _call_openai_compatible(
            messages,
            provider,
            config.api_key,
            base_url,
            model,
            timeout=timeout,
            thinking_enabled=thinking_enabled,
        ),
        provider,
        model,
    )


def mimo_day_replay(db, date: str | None = None) -> dict:
    """Generate a day replay with MiMo using only aggregate local activity data."""
    try:
        date = datetime.fromisoformat(date or "").strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        date = datetime.now().strftime("%Y-%m-%d")

    context = _compact_day_context(db, date)
    context["research_context"] = _research_context()

    try:
        raw_text, provider, model = _call_ai(_build_ai_messages(context))
    except Exception as exc:
        return _factual_replay(context, f"provider_error:{type(exc).__name__}")

    response, validation_error = _parse_ai_response(raw_text, context)
    if not response:
        return _factual_replay(context, validation_error)

    insights = response["insights"]
    findings = [item["text"] for item in insights]
    referenced_fact_ids = sorted({
        fact_id for item in insights for fact_id in item.get("fact_ids", [])
    })
    referenced_metric_ids = sorted({
        metric_id for item in insights for metric_id in item.get("metric_ids", [])
    })
    return {
        "ok": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "assistant": f"{_provider_name(provider, model)}手感回放",
        "model": model,
        "model_note": _provider_note(provider),
        "provider": provider,
        "interface": "POST /api/replay/jobs + GET /api/replay/jobs?id=...",
        "scope": "day",
        "date": date,
        "days": 1,
        "conclusion": response["conclusion"],
        "summary": context,
        "findings": findings,
        "judgments": findings,
        "insights": insights,
        "reflection_questions": response["reflection_questions"],
        "suggestions": response["suggestions"],
        "references": response["references"],
        "confidence": response["confidence"],
        "model_used": True,
        "fallback": False,
        "provenance": {
            "metric_version": context.get("metric_version"),
            "facts": context.get("facts", []),
            "fact_ids": referenced_fact_ids,
            "metric_ids": referenced_metric_ids,
            "boundary": "aggregate_data_with_cautious_interpretation",
            "available_fact_ids": [fact["fact_id"] for fact in context.get("facts", [])],
        },
        "sentences": insights,
        "raw_text": raw_text,
    }


def agent_evidence_payload(feed: dict, db=None) -> dict:
    """Build the outbound whitelist for weekly Agent deep reads."""
    packet = feed.get("evidence_pack") or {}
    facts = [
        {
            key: fact[key]
            for key in ("fact_id", "metric_id", "metric_version", "value", "unit")
            if key in fact
        }
        for fact in packet.get("facts", [])
        if isinstance(fact, dict)
    ]
    observations = [
        {
            key: item[key]
            for key in (
                "observation_id",
                "kind",
                "result",
                "conclusion",
                "prompt",
                "boundary",
                "fact_ids",
                "metric_ids",
                "claim_ids",
            )
            if key in item
        }
        for item in feed.get("observations", [])
        if isinstance(item, dict)
    ]
    payload = {
        "framework_version": packet.get("framework_version"),
        "purpose": packet.get("purpose"),
        "range": packet.get("range"),
        "coverage_gate": packet.get("coverage_gate"),
        "anchor_date": packet.get("anchor_date"),
        "facts": facts,
        "reference_status": packet.get("reference_status"),
        "observations": observations,
        "research_context": _research_context(),
        "user_corrections": feed.get("user_corrections", []),
    }
    if feed.get("daily_evidence"):
        payload["daily_evidence"] = feed["daily_evidence"]
    range_value = packet.get("range") or {}
    if db is not None and range_value.get("start") and range_value.get("end"):
        try:
            start = datetime.fromisoformat(str(range_value["start"]))
            end = datetime.fromisoformat(str(range_value["end"]))
            daily_summary = []
            current = start
            while current <= end and len(daily_summary) < 31:
                day = current.strftime("%Y-%m-%d")
                day_context = _compact_day_context(db, day)
                coverage = day_context.get("coverage") or {}
                top_apps = day_context.get("top_apps") or []
                daily_summary.append({
                    "date": day,
                    "input_events": int(day_context.get("responses") or 0),
                    "foreground_minutes": int(day_context.get("foreground_minutes") or 0),
                    "coverage_ratio": coverage.get("ratio"),
                    "coverage_status": coverage.get("status", "insufficient_data"),
                    "data_boundary": {
                        "peak_hour": day_context.get("peak_hour"),
                        "peak_range": day_context.get("peak_range"),
                        "top_hours": day_context.get("top_hours", []),
                        "segments": day_context.get("segments", []),
                    },
                    "top_app": top_apps[0].get("name", "") if top_apps else "",
                    "top_apps": top_apps[:3],
                })
                current += timedelta(days=1)
            payload["daily_summary"] = daily_summary
        except (TypeError, ValueError):
            pass
    return payload


def build_agent_deep_read_messages(feed: dict, db=None) -> list[dict]:
    payload = agent_evidence_payload(feed, db=db)
    override = agent_prompt_override()
    system = override or (
        "你是个人数字节律分析助手。根据给定的本地聚合数据，识别本周节律的连续性、切换、"
        "密度和变化。可以提出谨慎的状态解释、回想问题和低成本行动建议；解释必须使用"
        "概率措辞，不把相关性写成因果。研究卡片只提供分析视角，只引用实际使用过的 "
        "theory_id。"
        "不得虚构输入内容、窗口内容或具体任务，不得做医学或人格诊断，不得与其他用户排名，"
        "不得声称采集全天完整或绝对私密。不要输出内部推理过程。"
        "如果 user_corrections 中存在 correction，优先采用 corrected_text 的表述；"
        "ignore 或 retract 的观察不得出现在输出中。"
        "你的回复必须是一个 JSON 对象，键固定为 conclusion（字符串）、findings（数组，"
        "3-5 项，每项含 text、fact_ids、theory_ids、confidence）、reflection_questions"
        "（数组，1-2 项）、suggestions（数组，1-2 项）、references（数组，theory_id 字符串）。"
        "回想问题用温和开放的口吻，紧扣数据中具体的时间段、应用或密度变化，不假设具体工作内容，不用质问语气。"
        "不要输出 Markdown 代码块，不要输出解释文字，不要回显或复述输入数据。"
    )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                "请分析以下本地聚合记录。数据如下：\n"
                + json.dumps(payload, ensure_ascii=False)
            ),
        },
    ]


def agent_deep_read(
    feed: dict,
    thinking_enabled: bool = False,
    timeout: int | None = None,
    db=None,
) -> dict:
    """Run a weekly AI analysis; deterministic observations remain the fallback."""
    if not isinstance(feed, dict) or not feed.get("observations"):
        result = dict(feed or {})
        result.update({
            "model_used": False,
            "fallback": True,
            "fallback_reason": "no_observations",
            "provider": "rules",
        })
        return result

    packet = feed.get("evidence_pack") or {}
    try:
        raw_text, provider, model = _call_ai(
            build_agent_deep_read_messages(feed, db=db),
            timeout=timeout,
            thinking_enabled=thinking_enabled,
        )
    except Exception as exc:
        result = dict(feed)
        result.update({
            "model_used": False,
            "fallback": True,
            "fallback_reason": f"provider_error:{type(exc).__name__}",
            "provider": "rules",
            "thinking_enabled": bool(thinking_enabled),
        })
        return result

    agent_context = {
        "facts": packet.get("facts", []),
        "research_context": _research_context(),
        "metric_version": (
            packet["facts"][0].get("metric_version", "")
            if isinstance(packet.get("facts"), list) and packet["facts"]
            else ""
        ),
    }
    response, validation_error = _parse_ai_response(raw_text, agent_context)
    if not response:
        result = dict(feed)
        result.update({
            "model_used": False,
            "fallback": True,
            "fallback_reason": validation_error,
            "provider": "rules",
            "thinking_enabled": bool(thinking_enabled),
        })
        return result

    insights = response["insights"]
    return {
        "ok": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "surface": "weekly_agent_deep_read",
        "model_used": True,
        "provider": provider,
        "model": model,
        "thinking_enabled": bool(thinking_enabled),
        "fallback": False,
        "scope": "weekly",
        "purpose": packet.get("purpose"),
        "range": packet.get("range"),
        "evidence_pack": packet,
        "conclusion": response["conclusion"],
        "observations": insights,
        "findings": [item["text"] for item in insights],
        "reflection_questions": response["reflection_questions"],
        "suggestions": response["suggestions"],
        "references": response["references"],
        "confidence": response["confidence"],
        "theory_context": response["references"],
        "user_corrections": feed.get("user_corrections", []),
        "provenance": {
            "metric_version": agent_context["metric_version"],
            "fact_ids": sorted({
                fact_id
                for item in insights
                for fact_id in item.get("fact_ids", [])
            }),
            "metric_ids": sorted({
                metric_id
                for item in insights
                for metric_id in item.get("metric_ids", [])
            }),
            "claim_ids": sorted({
                str(item.get("claim_id") or "").upper()
                for item in insights
                if item.get("claim_id")
            }),
            "available_fact_ids": [
                fact.get("fact_id")
                for fact in packet.get("facts", [])
                if isinstance(fact, dict)
            ],
        },
        "sentences": insights,
        "raw_text": raw_text,
    }
