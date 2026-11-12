"""Расписание (дата и время матчей) и снятие команды с турнира (регламент, ст. 3.13)."""
from conftest import ADMIN, ev, finish, player_stats


def matches(client):
    return {m["id"]: m for m in client.get("/api/matches").json()}


def standings(client, league):
    return {r["team_id"]: r for r in client.get(f"/api/leagues/{league}/standings").json()}


def schedule(client, items, headers=None):
    return client.post("/api/matches/schedule", headers=ADMIN() if headers is None else headers, json={"items": items})


# ---------------- расписание ----------------

def test_assign_date_and_time_and_clear(client, mini):
    ab, cd = mini["matches"]["AB"], mini["matches"]["CD"]
    assert all(m["date_tbd"] for m in matches(client).values())                    # изначально «время уточняется»
    r = schedule(client, [{"match_id": ab, "match_date": "2026-11-28", "match_time": "15:00"},
                          {"match_id": cd, "match_date": "2026-11-28", "match_time": "16:00"}])
    assert r.status_code == 200 and r.json()["updated"] == 2 and r.json()["warnings"] == []
    m = matches(client)[ab]
    assert (m["date"], m["time"], m["weekday"], m["starts_at"], m["date_tbd"]) == ("28 ноября", "15:00", "сб", "2026-11-28T15:00", False)
    assert next(x for x in client.get("/api/matches/upcoming").json() if x["id"] == ab)["time"] == "15:00"
    schedule(client, [{"match_id": ab}])                                          # пустые поля снимают назначение
    assert matches(client)[ab]["date_tbd"] is True


def test_schedule_validation_and_warnings(client, mini):
    ab, cd = mini["matches"]["AB"], mini["matches"]["CD"]
    assert schedule(client, [{"match_id": ab, "match_date": "2026-11-28"}]).status_code == 400                              # нет времени
    assert schedule(client, [{"match_id": ab, "match_date": "2026-11-28", "match_time": "25:99"}]).status_code == 400
    assert schedule(client, [{"match_id": 99999, "match_date": "2026-11-28", "match_time": "15:00"}]).status_code == 404
    assert schedule(client, [], headers={}).status_code == 403
    r = schedule(client, [{"match_id": ab, "match_date": "2026-11-28", "match_time": "15:00"},
                          {"match_id": cd, "match_date": "2026-11-28", "match_time": "15:20"}])
    assert len(r.json()["warnings"]) == 1                                         # зал один: два матча почти одновременно


def test_played_matches_are_not_rescheduled(client, mini):
    ab = mini["matches"]["AB"]
    finish(client, ab, 1, 0)
    schedule(client, [{"match_id": ab, "match_date": "2030-01-01", "match_time": "10:00"}])
    assert matches(client)[ab]["starts_at"] is None


# ---------------- снятие команды ----------------

def test_withdraw_before_half_annuls_everything_and_restore_brings_it_back(client, mini):
    p, t, m, league = mini["players"], mini["teams"], mini["matches"], mini["league"]
    finish(client, m["AB"], 2, 1, motm=p["A1"], events=[ev(p["A1"], t["A"], "goal"), ev(p["B1"], t["B"], "red_card")])
    stats_before, table_before = player_stats(client), standings(client, league)
    preview = client.get(f"/api/teams/{t['A']}/withdraw-preview", headers=ADMIN()).json()
    assert (preview["mode"], preview["played"], preview["total"]) == ("annul", 1, 3)            # 1 из 3 = 33% < 50%
    assert client.post(f"/api/teams/{t['A']}/withdraw", json={"note": "x"}).status_code == 403   # без прав
    r = client.post(f"/api/teams/{t['A']}/withdraw", headers=ADMIN(), json={"note": "Решение №1"})
    assert r.status_code == 200 and r.json()["mode"] == "annul"
    assert client.post(f"/api/teams/{t['A']}/withdraw", headers=ADMIN(), json={}).status_code == 400   # повторно нельзя
    all_matches = matches(client)
    assert all(x["annulled"] for x in all_matches.values() if t["A"] in (x["home_team_id"], x["away_team_id"]))
    table = client.get(f"/api/leagues/{league}/standings").json()
    assert table[-1]["team_id"] == t["A"] and table[-1]["withdrawn"]                              # снятая — в конце таблицы
    assert standings(client, league)[t["B"]]["played"] == 0                                       # результат против неё аннулирован
    stats = player_stats(client)
    assert stats[p["A1"]]["goals"] == 0 and stats[p["B1"]]["red_cards"] == 0                      # голы и карточки обеих сторон
    assert not client.get(f"/api/leagues/{league}/disqualification").json()                       # автодисквалификация снята
    assert finish(client, m["AC"], 1, 0).status_code == 400                                       # в аннулированный матч результат не вносится
    assert all(t["A"] not in (x["home_team_id"], x["away_team_id"]) for x in client.get("/api/matches/upcoming").json())
    profile = client.get(f"/api/teams/{t['A']}").json()
    assert profile["withdrawn"] and profile["withdrawal_note"] == "Решение №1"
    assert client.post(f"/api/teams/{t['A']}/restore", headers=ADMIN()).status_code == 200
    assert client.post(f"/api/teams/{t['A']}/restore", headers=ADMIN()).status_code == 400
    assert player_stats(client) == stats_before                                                   # статистика как до снятия
    after = standings(client, league)
    assert all((after[k]["points"], after[k]["played"], after[k]["goal_difference"]) == (v["points"], v["played"], v["goal_difference"]) for k, v in table_before.items())


def test_withdraw_after_half_keeps_results_and_gives_technical_losses(client, mini):
    t, m, league = mini["teams"], mini["matches"], mini["league"]
    finish(client, m["AB"], 1, 1)
    finish(client, m["AC"], 1, 1)                                                                 # 2 из 3 = 67% >= 50%
    points_before = standings(client, league)[t["A"]]["points"]
    r = client.post(f"/api/teams/{t['A']}/withdraw", headers=ADMIN(), json={})
    assert r.json()["mode"] == "technical"
    ad = matches(client)[m["AD"]]
    assert (ad["technical"], ad["tech_reason"], ad["status"], ad["annulled"]) == (True, "withdrawal", "finished", False)
    assert (ad["home_score"], ad["away_score"]) == (0, 5)                                         # A играла дома — техпоражение 0:5
    assert matches(client)[m["AB"]]["home_score"] == 1                                            # сыгранные сохранены
    row = standings(client, league)[t["A"]]
    assert row["played"] == 3 and row["points"] == points_before and row["withdrawn"]
    assert client.post(f"/api/teams/{t['A']}/restore", headers=ADMIN()).status_code == 200
    ad = matches(client)[m["AD"]]
    assert (ad["status"], ad["technical"], ad["home_score"], ad["away_score"]) == ("scheduled", False, 0, 0)
    assert matches(client)[m["AB"]]["status"] == "finished"
