from pathlib import Path
import unittest
def _frontend_source() -> str:
    """index.html + 拆分后的 style.css / i18n.js / app.js 拼接（D9 机械三拆后测试仍以整体前端为合同对象）。"""
    static = Path(__file__).resolve().parents[1] / "static"
    parts = []
    for name in ("index.html", "style.css", "i18n.js", "app.js"):
        candidate = static / name
        if candidate.exists():
            parts.append(candidate.read_text(encoding="utf-8"))
    return "\n".join(parts)


ROOT = Path(__file__).resolve().parents[1]


class CalendarNavigationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = _frontend_source()

    def test_month_and_year_views_have_period_navigation(self):
        self.assertIn('id="heat-period-prev"', self.index)
        self.assertIn('id="heat-period-next"', self.index)
        self.assertIn("function changeHeatPeriod(delta)", self.index)
        self.assertIn("var month=heatMonthCursor", self.index)
        self.assertIn("var year=heatYearCursor", self.index)

    def test_calendar_cells_select_a_specific_date(self):
        self.assertIn("function jumpToHeatDate(date)", self.index)
        self.assertIn("data-date=", self.index)
        self.assertIn("jumpToHeatDate(cell.dataset.date||'')", self.index)
        self.assertIn("d.date===selectedDate?' selected':'", self.index)

    def test_year_month_label_drills_into_month_view(self):
        self.assertIn("function drillIntoMonth(month)", self.index)
        self.assertIn('class="year-month-label" data-month=', self.index)
        self.assertIn("drillIntoMonth(button.dataset.month||'')", self.index)


if __name__ == "__main__":
    unittest.main()
