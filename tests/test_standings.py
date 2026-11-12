"""Турнирная таблица по регламенту (ст. 8.1)."""
import main
from conftest import ev, finish


class M:  # упрощённый матч для sort_standings
    def __init__(self, home, away, hs, as_):
        self.home_team_id, self.away_team_id, self.home_score, self.away_score = home, away, hs, as_


def row(tid, name, pts=3, gd=0, wins=1, gf=2, red=0, yellow=0):
    return {"team_id": tid, "team_name": name, "points": pts, "goal_difference": gd, "wins": wins, "goals_for": gf,
            "red_cards": red, "yellow_cards": yellow}


def standings(client, league):
    return client.get(f"/api/leagues/{league}/standings").json()


def test_alphabetical_before_the_tournament_starts(client, mini):
    names = [r["team_name"] for r in standings(client, mini["league"])]
    assert names == sorted(names)


def test_points_then_goal_difference(client, mini):
    m = mini["matches"]
    finish(client, m["AB"], 1, 0)   # A: 3 очка, разница +1
    finish(client, m["CD"], 3, 0)   # C: 3 очка, разница +3
    order = [r["team_name"] for r in standings(client, mini["league"])]
    assert order[:2] == ["Команда C", "Команда A"]


def test_head_to_head_decides_before_cards():
    rows = [row(1, "Альфа"), row(2, "Бета")]
    assert [r["team_name"] for r in main.sort_standings(rows, [M(1, 2, 0, 1)])] == ["Бета", "Альфа"]   # Бета выиграла личную встречу


def test_circular_tie_falls_back_to_reds_then_yellows_then_alphabet():
    rows = [row(1, "Альфа", red=1), row(2, "Бета"), row(3, "Гамма")]
    circular = [M(1, 2, 0, 1), M(2, 3, 0, 1), M(3, 1, 0, 1)]
    assert [r["team_name"] for r in main.sort_standings(rows, circular)] == ["Бета", "Гамма", "Альфа"]   # у Альфы красная — ниже
    rows = [row(1, "Альфа", yellow=3), row(2, "Бета", yellow=1)]
    assert [r["team_name"] for r in main.sort_standings(rows, [M(1, 2, 1, 1)])] == ["Бета", "Альфа"]    # ничья: меньше жёлтых — выше
    rows = [row(2, "Бета"), row(1, "Альфа")]
    assert [r["team_name"] for r in main.sort_standings(rows, [])] == ["Альфа", "Бета"]                 # полное равенство — по алфавиту


def test_red_cards_count_in_team_stats(client, mini):
    p, t = mini["players"], mini["teams"]
    finish(client, mini["matches"]["AB"], 0, 0, events=[ev(p["A1"], t["A"], "red_card", 30)])
    team_a = next(r for r in standings(client, mini["league"]) if r["team_id"] == t["A"])
    assert team_a["red_cards"] == 1
