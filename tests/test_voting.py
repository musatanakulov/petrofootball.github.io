"""Голосование за «Игрока тура»: окно с понедельника 00:00 до вторника 00:00, правила голоса."""
from conftest import ADMIN, finish, tg


def finish_round_one(client, mini):
    m, p = mini["matches"], mini["players"]
    finish(client, m["AB"], 2, 1, motm=p["A1"])
    finish(client, m["CD"], 0, 0, motm=p["D1"])


def polls(client, mini, headers=None):
    return client.get(f"/api/leagues/{mini['league']}/polls", headers=headers or {}).json()


def vote(client, mini, player, user):
    return client.post("/api/polls/vote", headers=tg(user), json={"league_id": mini["league"], "round_name": "Тур 1", "player_id": mini["players"][player]})


def test_no_poll_until_the_round_is_complete(client, mini, clock):
    finish(client, mini["matches"]["AB"], 1, 0, motm=mini["players"]["A1"])
    assert polls(client, mini)["polls"] == []                    # сыграна половина тура


def test_window_is_monday_to_tuesday_24h(client, mini, clock):
    clock(2026, 11, 11, 12)                                      # среда
    finish_round_one(client, mini)
    poll = polls(client, mini)["polls"][0]
    assert poll["status"] == "upcoming" and poll["candidates"] == [] and poll["candidates_count"] == 2
    assert (poll["opens_at"], poll["closes_at"]) == ("2026-11-16T00:00:00+05:00", "2026-11-17T00:00:00+05:00")
    clock(2026, 11, 15, 23, 59)
    assert polls(client, mini)["polls"][0]["status"] == "upcoming"
    clock(2026, 11, 16, 0, 0)
    assert polls(client, mini)["polls"][0]["status"] == "active"
    clock(2026, 11, 16, 23, 59, 59)
    assert polls(client, mini)["polls"][0]["status"] == "active"
    clock(2026, 11, 17, 0, 0)
    assert polls(client, mini)["polls"][0]["status"] == "closed"


def test_voting_rules(client, mini, clock):
    clock(2026, 11, 11); finish_round_one(client, mini)
    clock(2026, 11, 15, 23, 59)
    assert vote(client, mini, "A1", 2001).status_code == 403     # ещё не началось
    clock(2026, 11, 16, 0, 0)
    poll = polls(client, mini, tg(2001))["polls"][0]
    assert {c["name"] for c in poll["candidates"]} == {"Игрок A1", "Игрок D1"}      # кандидаты — «игроки матча» тура
    assert poll["can_vote"] and not poll["results_visible"]
    assert client.get(f"/api/leagues/{mini['league']}/polls").json()["polls"][0]["can_vote"] is False   # аноним не голосует
    assert client.post("/api/polls/vote", json={"league_id": mini["league"], "round_name": "Тур 1", "player_id": mini["players"]["A1"]}).status_code == 403
    r = vote(client, mini, "A1", 2001)
    assert r.status_code == 200 and r.json()["poll"]["my_vote"] == mini["players"]["A1"]
    assert vote(client, mini, "D1", 2001).status_code == 409     # один голос на человека
    assert vote(client, mini, "B1", 2002).status_code == 400     # не кандидат
    assert client.post("/api/polls/vote", headers=tg(2002), json={"league_id": mini["league"], "round_name": "Нет", "player_id": 1}).status_code == 404


def test_results_hidden_until_vote_or_close_and_tie_shows_both_winners(client, mini, clock):
    clock(2026, 11, 11); finish_round_one(client, mini)
    clock(2026, 11, 16, 8)
    assert vote(client, mini, "A1", 2001).status_code == 200
    assert vote(client, mini, "D1", 2002).status_code == 200
    anon = polls(client, mini)["polls"][0]
    assert not anon["results_visible"] and all("votes" not in c for c in anon["candidates"])
    clock(2026, 11, 17, 0, 1)
    closed = polls(client, mini)["polls"][0]
    assert closed["results_visible"] and closed["total_votes"] == 2
    assert {c["name"] for c in closed["candidates"] if c["is_winner"]} == {"Игрок A1", "Игрок D1"}
    assert vote(client, mini, "A1", 2003).status_code == 403     # голосование закрыто


def test_round_completed_on_monday_opens_next_week(client, mini, clock):
    clock(2026, 11, 16, 10)                                      # понедельник
    finish_round_one(client, mini)
    assert polls(client, mini)["polls"][0]["opens_at"] == "2026-11-23T00:00:00+05:00"


def test_editing_a_result_does_not_shift_the_window(client, mini, clock):
    clock(2026, 11, 11); finish_round_one(client, mini)
    opens = polls(client, mini)["polls"][0]["opens_at"]
    clock(2026, 11, 20, 9)
    finish(client, mini["matches"]["AB"], 3, 1, motm=mini["players"]["A1"])
    assert polls(client, mini)["polls"][0]["opens_at"] == opens


def test_admin_can_close_a_round_with_a_postponed_match(client, mini, clock):
    """Перенесённый матч не должен блокировать голосование: админ закрывает тур."""
    clock(2026, 11, 11)
    finish(client, mini["matches"]["AB"], 1, 0, motm=mini["players"]["A1"])        # CD не сыгран (перенесён)
    assert polls(client, mini)["polls"] == []
    url = f"/api/leagues/{mini['league']}/rounds/Тур 1/close"
    assert client.post(url).status_code == 403                                     # только админ
    assert client.post(url, headers=ADMIN()).status_code == 200
    poll = polls(client, mini)["polls"][0]
    assert poll["status"] == "upcoming" and poll["candidates_count"] == 1
    assert poll["opens_at"] == "2026-11-16T00:00:00+05:00"
    assert client.delete(url, headers=ADMIN()).status_code == 200                  # можно отменить
    assert polls(client, mini)["polls"] == []
