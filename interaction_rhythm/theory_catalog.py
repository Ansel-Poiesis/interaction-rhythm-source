"""Reviewed theory cards packaged with the Agent surface.

Each card is a bounded interpretive lens: it can frame an observation, but it
must never change the underlying fact, result, or boundary.
"""
from __future__ import annotations

from interaction_rhythm.agent_observations import TheoryCard


CATALOG_VERSION = "0.1.3"
REVIEWED_THEORY_CARDS: tuple[TheoryCard, ...] = (
    TheoryCard(
        theory_id="stage-based-personal-informatics",
        title="A stage-based model of personal informatics systems",
        source_url="https://doi.org/10.1145/1753326.1753409",
        evidence_level="R2",
        lens="稳定覆盖只说明这周的数据足够进入后续整理阶段，不说明已经发生改变。",
        original_domain="personal informatics",
        transfer_boundary="This model explains stages in personal informatics. It does not establish causality, goal attainment, or productivity.",
        observation_kinds=("coverage",),
        required_metric_ids=("IR-HLT-003",),
        minimum_valid_days=0,
    ),
    TheoryCard(
        theory_id="quantified-self-collection-exploration",
        title="Understanding quantified-selfers' practices in collecting and exploring personal data",
        source_url="https://doi.org/10.1145/2556288.2557372",
        evidence_level="R1",
        lens="收集和探索是两件不同的事；当前只说明记录已经稳定到可以继续被探索。",
        original_domain="quantified self",
        transfer_boundary="This study describes how experienced self-trackers collect and explore data. It does not prove attention, performance, or intent from one week of coverage.",
        observation_kinds=("coverage",),
        required_metric_ids=("IR-HLT-003",),
        minimum_valid_days=7,
    ),
    TheoryCard(
        theory_id="flexible-minimalist-self-tracking",
        title="Flexible Minimalist Self-Tracking to Support Individual Reflection",
        source_url="https://doi.org/10.1145/3660339",
        evidence_level="R1",
        lens="低负担、可回看的记录方式，只有在覆盖仍然够稳时，才更适合支撑反思。",
        original_domain="self-tracking reflection",
        transfer_boundary="This article supports lightweight self-tracking for reflection. It does not justify causal claims about mood, focus, or output.",
        observation_kinds=("coverage",),
        required_metric_ids=("IR-HLT-003",),
        minimum_valid_days=8,
    ),
    TheoryCard(
        theory_id="reflection-nature-critical-examination",
        title="Personal informatics and reflection: a critical examination of the nature of reflection",
        source_url="https://doi.org/10.1145/2468356.2468715",
        evidence_level="R2",
        lens="个人基线给出了可比较的相对位置，但它仍然只是在解释差异，不是在解释原因。",
        original_domain="personal informatics reflection",
        transfer_boundary="This work frames reflection as examination and comparison. It does not turn a baseline position into causality, intent, or outcome.",
        observation_kinds=("reference_position",),
        required_metric_ids=("IR-BAS-001", "IR-BAS-002", "IR-HLT-003"),
        minimum_valid_days=7,
    ),
    TheoryCard(
        theory_id="people-like-me-cohort-comparison",
        title="People Like Me: Designing for Reflection on Aggregate Cohort Data in Personal Informatics Systems",
        source_url="https://doi.org/10.1145/3264917",
        evidence_level="R2",
        lens="当指标高于或低于个人典型范围时，最稳妥的读法仍是相对位置，而不是优劣或因果。",
        original_domain="aggregate cohort reflection",
        transfer_boundary="This work supports comparison-oriented reflection on data. It does not justify social ranking, productivity claims, or value judgments.",
        observation_kinds=("reference_position",),
        required_metric_ids=("IR-EVT-004", "IR-BAS-001", "IR-BAS-002", "IR-HLT-003"),
        minimum_valid_days=14,
    ),
    TheoryCard(
        theory_id="reflection-theory-practice-gaps",
        title="Reflection in Theory and Reflection in Practice: An Exploration of the Gaps in Reflection Support among Personal Informatics Apps",
        source_url="https://doi.org/10.1145/3491102.3501991",
        evidence_level="R2",
        lens="参考位置只有在足够稳定时才适合被反思；单个偏移不该被过度解读。",
        original_domain="reflection support",
        transfer_boundary="This work studies reflection support in personal informatics apps. It does not infer mood, efficiency, or causal change from one week of baseline comparison.",
        observation_kinds=("reference_position",),
        required_metric_ids=("IR-FG-001", "IR-BAS-001", "IR-BAS-002", "IR-HLT-003"),
        minimum_valid_days=14,
    ),
    TheoryCard(
        theory_id="daily-mood-variability-stability",
        title="The stability of mood variability: A spectral analytic approach to daily mood assessments",
        source_url="https://doi.org/10.1037/0022-3514.52.6.1195",
        evidence_level="R1",
        lens="日与日之间的起伏本身是一种稳定特征；本周极差只描述这七天内的波动，不解释原因，也不代表某一天更有价值。",
        original_domain="daily mood variability",
        transfer_boundary="This study examines the stability of daily mood variability over time. It does not explain why a week's input range widened, nor does it rank days by worth or effort.",
        observation_kinds=("daily_variation",),
        required_metric_ids=("IR-EVT-004",),
        minimum_valid_days=2,
    ),
    TheoryCard(
        theory_id="weekend-recovery-rituals",
        title="Recovery, health, and job performance: Effects of weekend experiences",
        source_url="https://doi.org/10.1037/1076-8998.10.3.187",
        evidence_level="R1",
        lens="周末与工作日的差异可以反映恢复节奏；但差异只描述记录分布，不代表哪一段安排更好或更健康。",
        original_domain="occupational recovery",
        transfer_boundary="This work studies how weekend recovery experiences relate to well-being and performance. It does not infer recovery quality, mood, or health from one week of input counts.",
        observation_kinds=("weekend_pattern",),
        required_metric_ids=("IR-EVT-004",),
        minimum_valid_days=2,
    ),
)
