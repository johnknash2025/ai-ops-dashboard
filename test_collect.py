"""Tests for the review findings on backlog-20: interval timers, cron/event counting consistency,
unit-name normalization, Python version guard, and 24h chart coverage notes."""
import re
import shutil
import subprocess
import sys

import collect


def set_cfg(events=(), hosts=("alpha",), hub="hub"):
    cfg = {"hub": {"name": hub, "uses": []},
           "hosts": [{"name": h, "ssh": []} for h in hosts],
           "events": [dict(e) for e in events]}
    collect.CFG.clear()
    collect.CFG.update(cfg)
    collect.COLORS.clear()
    collect.COLORS.update({h: "#888" for h in hosts})
    collect.COLORS[hub] = "#6b5bd6"
    collect.NOTES.clear()


def timer(unit, result="success", last="Thu 2026-10-29 07:00:00 JST", schedule="OnCalendar=*-*-* 07:30:00"):
    return {"unit": unit, "description": "desc", "next": "Fri 2026-10-30 07:30:00 JST", "last": last,
            "result": result, "exit": "0", "schedule": schedule, "llm": {"ports": []}}


def machine(timers=(), cron=()):
    return {"host": "alpha-box", "timers": list(timers), "services": [], "cron": list(cron),
            "llm_servers": [], "gpu": "gpu", "mem": "8G", "uptime": "1d"}


def nums(html):
    return [int(m) for m in re.findall(r'<div class=num[^>]*>(\d+)</div>', html)]


def hub_m():
    return {"uses": [], "agents": [], "tunnels": []}


def test_interval_timers_return_none_with_phrase():
    set_cfg()
    for schedule, want in [("OnActiveSec=10min", "10分ごと"),
                           ("OnBootSec=5min", "5分ごと"),
                           ("OnUnitActiveSec=1h", "1時間ごと"),
                           ("OnUnitInactiveSec=30min", "30分ごと")]:
        times, phrase = collect.run_times(schedule)
        assert times is None, schedule
        assert phrase == want, (schedule, phrase)
    times, phrase = collect.run_times("OnUnitActiveUSec=500000")
    assert times is None and "500000" in phrase and phrase != "（不明）"
    assert collect.timer_phrase("OnActiveSec=10min") == "10分ごと"


def test_calendar_word_schedules():
    set_cfg()
    assert collect.run_times("OnCalendar=weekly") == (None, "毎週")
    assert collect.run_times("OnCalendar=daily") == (None, "毎日")
    assert collect.run_times("OnCalendar=*-*-* 07:30:00")[1] == "毎日 7:30"
    assert collect.run_times("0 7 * * * sleep 10") == ([], "（不明）")


def test_interval_strip_shows_band_and_phrase():
    set_cfg()
    out = collect.strip("OnActiveSec=10min", "#888")
    assert "10分ごと" in out
    assert 'width="160"' in out


def test_unit_stem_matches_across_suffixes():
    assert collect.unit_stem("foo.timer") == "foo"
    assert collect.unit_stem("foo.service") == "foo"
    assert collect.unit_stem("foo.path") == collect.unit_stem("foo") == "foo"
    set_cfg(events=[{"name": "nightly", "source": "github", "match": "push", "unit": "foo.timer"}])
    assert collect.is_event(timer("foo.service"))
    assert collect.is_event(timer("foo.timer"))
    assert collect.triggers("foo") == collect.CFG["events"]


def test_cron_on_event_side_is_counted():
    set_cfg(events=[{"name": "nightly", "source": "github", "match": "push", "unit": "nightly-review.timer"}])
    data = {"alpha": machine(cron=["17 3 * * * /home/x/nightly-review.py"])}
    per_n, ev_n = collect.job_counts(data["alpha"])
    assert (per_n, ev_n) == (0, 1)
    sections = list(collect.out_timers(data))
    assert "nightly-review" in sections[1] and "⚡" in sections[1]
    assert "nightly-review" not in sections[0]
    home, jobs, _ = collect.overview(data)
    assert " · 失敗" not in home
    assert "イベントの仕事 · cron 1 件結果なし" in home
    idx = home.index("定期 失敗")
    assert re.search(r'<div class=num[^>]*>0</div>定期 失敗', home), home[max(0, idx - 120):idx + 30]
    assert collect.overview(data)[0].count("cron 1 件結果なし") == 1


def test_per_counts_equal_ok_plus_bad_with_cron():
    set_cfg()
    data = {"alpha": machine(timers=[timer("a.service", "success"), timer("b.service", "failed")],
                             cron=["5 4 * * * /home/x/report.sh"])}
    rows = collect.host_rows(data["alpha"], collect.event_units(data))
    assert len(rows) == 3
    per_n, ev_n = collect.job_counts(data["alpha"])
    ok = sum(1 for s, hr, o in rows if not s and o)
    bad = sum(1 for s, hr, o in rows if not s and hr and not o)
    assert (per_n, ev_n, ok, bad) == (3, 0, 2, 1)
    assert per_n == ok + bad
    home, _, _ = collect.overview(data)
    assert nums(home)[:4] == [0, 3, 0, 2]
    assert "cron 1 件結果なし" in home
    idx = home.index("定期 失敗")
    assert re.search(r'<div class=num[^>]*>1</div>定期 失敗', home), home[max(0, idx - 120):idx + 30]


def test_event_side_failure_still_counted():
    set_cfg(events=[{"name": "hook", "source": "github", "match": "push", "unit": "hook-in"}])
    data = {"alpha": machine(timers=[timer("hook-in.service", "failed")])}
    home, jobs, _ = collect.overview(data)
    assert "失敗 1" in home
    assert "⚡" in jobs and "hook-in" in jobs


def test_chart_notes_jobs_with_unknown_time():
    set_cfg()
    data = {"alpha": machine(timers=[timer("good.service", "success"),
                                     timer("weird.service", "success", last="不明")])}
    _, jobs, _ = collect.overview(data)
    assert "1 件は時刻不明" in jobs
    assert jobs.count("<circle") == 1


def test_microsecond_intervals_do_not_read_as_seconds():
    set_cfg()
    times, phrase = collect.run_times("OnUnitActiveUSec=500000")
    assert times is None
    assert phrase == "500000µ秒（500ms）ごと"
    assert collect.run_times("OnUnitActiveUSec=900")[1] == "900µ秒ごと"
    assert collect.run_times("OnUnitActiveSec=45")[1] == "45秒ごと"


def test_event_rule_without_unit_never_moves_a_timer_to_event_side():
    set_cfg(events=[{"name": "ghost", "source": "github", "match": "push"}])
    assert collect.event_units() == set()
    assert not collect.is_event(timer("ghost.timer"))
    data = {"alpha": machine(timers=[timer("ghost.timer")])}
    per_n, ev_n = collect.job_counts(data["alpha"])
    assert (per_n, ev_n) == (1, 0)
    per_tbl, ev_tbl = list(collect.out_timers(data))
    assert "ghost" in per_tbl and "ghost" not in ev_tbl


def test_render_separates_periodic_and_event_blocks_per_machine():
    set_cfg(events=[{"name": "hook", "source": "github", "match": "push", "unit": "hook-in.timer"}])
    data = {"alpha": machine(timers=[timer("tick.service"), timer("hook-in.service", "failed")])}
    page = collect.render(data, hub_m())
    per = re.search(r'<div class="blk per">.*?</table></div>', page, re.S)
    ev = re.search(r'<div class="blk ev">.*?</table></div>', page, re.S)
    assert per and ev
    assert "tick" in per.group(0) and "hook-in" not in per.group(0)
    assert "hook-in" in ev.group(0) and "tick" not in ev.group(0)
    assert "github" in ev.group(0) and "🕒" in per.group(0) and "⚡" in ev.group(0)


def test_render_survives_record_without_cron_key():
    set_cfg()
    rec = machine(timers=[timer("tick.service")])
    rec.pop("cron")
    page = collect.render({"alpha": rec}, hub_m())
    assert "cron" in page and "定期の仕事" in page


def test_overview_survives_record_without_timers_key():
    set_cfg()
    rec = {"host": "alpha-box", "cron": [], "services": [], "llm_servers": [],
           "gpu": "gpu", "mem": "8G", "uptime": "1d"}
    home, jobs, _ = collect.overview({"alpha": rec})
    assert "時刻不明" not in jobs
    assert "定期の仕事" in home


def test_out_timers_event_table_has_trigger_column_and_periodic_table_has_when_column():
    set_cfg(events=[{"name": "hook", "source": "github", "match": "push", "unit": "hook-in.timer"}])
    data = {"alpha": machine(timers=[timer("tick.service"), timer("hook-in.service", "failed")])}
    per_tbl, ev_tbl = list(collect.out_timers(data))
    assert per_tbl.startswith('<section class=periodic>') and "<th>いつ</th>" in per_tbl
    assert "tick" in per_tbl and "hook-in" not in per_tbl
    assert ev_tbl.startswith('<section class=event>') and "<th>引き金</th>" in ev_tbl
    assert "hook-in" in ev_tbl and "tick" not in ev_tbl and "⚡" in ev_tbl


def test_out_timers_event_side_falls_back_when_no_event_jobs():
    set_cfg()
    data = {"alpha": machine(timers=[timer("tick.service")])}
    _, ev_tbl = list(collect.out_timers(data))
    assert ev_tbl.startswith('<section class=event>')
    assert 'hosts.json の "events"' in ev_tbl


def test_python_version_guard_comes_before_imports():
    for name, import_marker in [("collect.py", "import assets"), ("serve.py", "import chat")]:
        src = (collect.HERE / name).read_text()
        guard = src.index("sys.version_info < (3, 12)")
        assert guard < src.index(import_marker), name


def test_python_version_guard_message_under_older_python():
    old = next((p for p in ("python3.10", "python3.11", "python3")
                if shutil.which(p) and _version(shutil.which(p)) < (3, 12)), None)
    if not old:
        return
    binary = shutil.which(old)
    assert binary
    r = subprocess.run([binary, str(collect.HERE / "collect.py")], capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    assert "3.12" in r.stderr + r.stdout
    assert "SyntaxError" not in r.stderr


def _version(binary):
    out = subprocess.run([binary, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
                         capture_output=True, text=True, timeout=30).stdout.split()
    return tuple(int(x) for x in out)
