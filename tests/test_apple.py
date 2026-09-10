from nest import apple
from nest.data import BRIEFING_KEYS, format_briefing, parse_briefing


def test_calendar_lines_sorted_allday_first():
    raw = "14:00\tLunch\tWork\n\tHoliday\tHome\n09:30\tStandup\tWork\n"
    assert apple.parse_calendar_output(raw) == ["Holiday", "09:30 Standup", "14:00 Lunch"]


def test_reminders_overdue_first_with_marker():
    raw = "0\tBuy milk\n1\tCall bank\n0\t\n"
    assert apple.parse_reminders_output(raw) == ["! Call bank", "Buy milk"]


def test_agenda_text_caps_items():
    agenda = apple.Agenda([f"{i:02d}:00 e{i}" for i in range(10)], [f"r{i}" for i in range(10)], 0)
    lines = agenda.text().splitlines()
    assert len(lines) == apple.MAX_ITEMS
    assert lines[0] == "00:00 e0"


def test_briefing_roundtrip_uses_tasks_url():
    assert "tasks_url" in BRIEFING_KEYS and "todoist_token" not in BRIEFING_KEYS
    text = format_briefing({"enabled": "on", "city": "Tashkent", "tasks_url": "http://10.0.0.2:8787/api/briefing/tasks.txt"})
    values = parse_briefing(text)
    assert values["enabled"] == "1"
    assert values["tasks_url"].endswith("/api/briefing/tasks.txt")


def test_calendar_filter_defaults_to_skipping_mirrors():
    assert apple._calendar_filter([]) == 'cname is not in {"Scheduled Reminders", "Siri Suggestions"}'
    assert apple._calendar_filter(["Work"]) == 'cname is in {"Work"}'
