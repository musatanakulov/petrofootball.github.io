"""Карточки, дисквалификации и срок их действия (регламент, ст. 11)."""
from datetime import timedelta

from conftest import ADMIN, ev, finish, player_stats


def disq(client, league):
    return {x["player_id"]: x for x in client.get(f"/api/leagues/{league}/disqualification").json()}


def test_two_yellows_in_one_match_become_red_and_zero_yellows(client, mini):
    p, t = mini["players"], mini["teams"]
    r = finish(client, mini["matches"]["AB"], 0, 0, events=[ev(p["A1"], t["A"], "yellow_card", 10), ev(p["A1"], t["A"], "yellow_card", 55)])
    assert r.status_code == 200 and "Вторая жёлтая" in r.json()["message"]
    stats = player_stats(client)[p["A1"]]
    assert (stats["yellow_cards"], stats["red_cards"]) == (0, 1)     # обе жёлтые «сгорели»
    s = disq(client, mini["league"])[p["A1"]]["sanctions"][0]
    assert (s["reason"], s["matches"], s["served"], s["auto"]) == ("За второе предупреждение в матче", 1, 0, "second_yellow")


def test_one_yellow_is_just_a_yellow(client, mini):
    p, t = mini["players"], mini["teams"]
    finish(client, mini["matches"]["AB"], 0, 0, events=[ev(p["A1"], t["A"], "yellow_card")])
    stats = player_stats(client)[p["A1"]]
    assert (stats["yellow_cards"], stats["red_cards"]) == (1, 0)
    assert p["A1"] not in disq(client, mini["league"])


def test_three_yellows_in_one_match_rejected_and_nothing_saved(client, mini):
    p, t, mid = mini["players"], mini["teams"], mini["matches"]["AB"]
    r = finish(client, mid, 0, 0, events=[ev(p["A1"], t["A"], "yellow_card")] * 3)
    assert r.status_code == 400 and "три жёлтые" in r.json()["detail"].lower()
    assert next(m for m in client.get("/api/matches").json() if m["id"] == mid)["status"] == "scheduled"


def test_invalid_card_combinations_rejected(client, mini):
    p, t, mid = mini["players"], mini["teams"], mini["matches"]["AB"]
    yellow, red = ev(p["A1"], t["A"], "yellow_card"), ev(p["A1"], t["A"], "red_card")
    assert finish(client, mid, 0, 0, events=[yellow, yellow, red]).status_code == 400   # уже удалён по второй жёлтой
    assert finish(client, mid, 0, 0, events=[red, red]).status_code == 400              # дважды удалён
    assert finish(client, mid, 0, 0, events=[ev(p["C1"], t["C"], "yellow_card")]).status_code == 400   # игрок не из этого матча
    assert finish(client, mid, 0, 0, events=[ev(p["A1"], t["A"], "foul")]).status_code == 400


def test_score_and_minute_limits(client, mini):
    p, t, mid = mini["players"], mini["teams"], mini["matches"]["AB"]
    assert finish(client, mid, -1, 0).status_code == 422
    assert finish(client, mid, 0, -3).status_code == 422
    assert finish(client, mid, 100, 0).status_code == 422
    assert finish(client, mid, 1, 0, events=[ev(p["A1"], t["A"], "goal", 61)]).status_code == 422
    assert finish(client, mid, 1, 0, events=[ev(p["A1"], t["A"], "goal", 60)]).status_code == 200


def test_ban_expires_after_the_teams_next_match(client, mini):
    p, t, m, league = mini["players"], mini["teams"], mini["matches"], mini["league"]
    finish(client, m["AB"], 0, 0, events=[ev(p["A1"], t["A"], "yellow_card"), ev(p["A1"], t["A"], "yellow_card")])
    suspended = lambda mid: {x["player_id"] for x in client.get(f"/api/matches/{mid}/suspended").json()}  # noqa: E731
    assert p["A1"] not in suspended(m["AB"])      # матч, где получена карточка
    assert p["A1"] in suspended(m["AC"])          # следующий матч — пропускает
    assert p["A1"] not in suspended(m["AD"])      # через один — уже нет
    assert p["A1"] in disq(client, league)
    finish(client, m["AC"], 0, 0)
    assert p["A1"] not in disq(client, league)    # срок вышел — игрок исчез из списка


def test_straight_red_is_automatic_one_match_ban(client, mini):
    p, t = mini["players"], mini["teams"]
    finish(client, mini["matches"]["AB"], 0, 0, events=[ev(p["A2"], t["A"], "red_card", 40)])
    s = disq(client, mini["league"])[p["A2"]]["sanctions"][0]
    assert (s["matches"], s["auto"], s["reason"]) == (1, "red", "Прямая красная карточка")


def test_three_yellows_in_different_matches_give_one_match_ban(client, mini):
    p, t, m, league = mini["players"], mini["teams"], mini["matches"], mini["league"]
    yellow = [ev(p["A1"], t["A"], "yellow_card")]
    finish(client, m["AB"], 0, 0, events=yellow)
    finish(client, m["AC"], 0, 0, events=yellow)
    assert p["A1"] not in disq(client, league)
    finish(client, m["AD"], 0, 0, events=yellow)
    s = disq(client, league)[p["A1"]]["sanctions"][0]
    assert (s["auto"], s["matches"]) == ("yellows", 1)
    finish(client, m["AD"], 0, 0)                  # правка результата: третьей жёлтой больше нет
    assert p["A1"] not in disq(client, league)


def test_admin_edit_of_auto_sanction_survives_resave(client, mini):
    p, t, mid, league = mini["players"], mini["teams"], mini["matches"]["AB"], mini["league"]
    events = [ev(p["A1"], t["A"], "yellow_card"), ev(p["A1"], t["A"], "yellow_card")]
    finish(client, mid, 0, 0, events=events)
    sid = disq(client, league)[p["A1"]]["sanctions"][0]["id"]
    assert client.put(f"/api/sanctions/{sid}", headers=ADMIN(), json={"reason": "За второе предупреждение в матче", "matches": 2}).status_code == 200
    finish(client, mid, 0, 0, events=events)
    sanctions = disq(client, league)[p["A1"]]["sanctions"]
    assert len(sanctions) == 1 and sanctions[0]["matches"] == 2


def test_manual_sanctions_by_date_text_and_matches(client, mini, clock):
    clock(2026, 11, 11)
    p, league = mini["players"], mini["league"]
    add = lambda body: client.post("/api/sanctions", headers=ADMIN(), json={"player_id": p["B1"], **body})  # noqa: E731
    assert add({"reason": "вчера", "until_date": "2026-11-10"}).status_code == 200
    assert add({"reason": "сегодня", "until_date": "2026-11-11"}).status_code == 200
    assert add({"reason": "текстом", "term": "до решения КДК"}).status_code == 200
    reasons = {s["reason"] for s in disq(client, league)[p["B1"]]["sanctions"]}
    assert reasons == {"сегодня", "текстом"}                       # «вчера» уже истекла
    assert add({"reason": "x", "matches": 1, "until_date": "2026-11-12"}).status_code == 400    # матчи и дата вместе
    assert add({"reason": "x", "matches": 0}).status_code in (400, 422)
    assert add({"reason": "  ", "term": ""}).status_code == 400                                  # пусто
    assert client.post("/api/sanctions", headers={}, json={"player_id": p["B1"], "reason": "x"}).status_code == 403


def test_reset_match_rolls_back_everything(client, mini):
    p, t, mid = mini["players"], mini["teams"], mini["matches"]["AB"]
    before = player_stats(client)
    finish(client, mid, 2, 1, motm=p["A1"], events=[ev(p["A1"], t["A"], "goal"), ev(p["A2"], t["A"], "yellow_card"), ev(p["A2"], t["A"], "yellow_card")])
    assert player_stats(client) != before
    assert client.post(f"/api/matches/{mid}/reset", headers=ADMIN()).status_code == 200
    assert player_stats(client) == before                                    # статистика всех игроков как была
    assert not disq(client, mini["league"])                                  # автодисквалификация снята
    m = next(x for x in client.get("/api/matches").json() if x["id"] == mid)
    assert (m["status"], m["home_score"], m["away_score"], m["events"]) == ("scheduled", 0, 0, [])


def test_technical_result_rules(client, mini):
    p, t, mid = mini["players"], mini["teams"], mini["matches"]["AB"]
    assert finish(client, mid, 0, 3, technical=True).status_code == 400      # только 5:0 / 0:5
    r = finish(client, mid, 0, 5, technical=True, motm=p["A1"], events=[ev(p["A1"], t["A"], "goal")])
    assert r.status_code == 200
    m = next(x for x in client.get("/api/matches").json() if x["id"] == mid)
    assert m["technical"] and m["events"] == [] and m["man_of_the_match"] is None
    assert player_stats(client)[p["A1"]]["goals"] == 0                       # события отброшены
    finish(client, mid, 1, 0)                                                # обычный результат снимает флаг
    assert not next(x for x in client.get("/api/matches").json() if x["id"] == mid)["technical"]
