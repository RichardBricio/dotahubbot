import itertools
from database import get_player

def balance_teams(players):
    best_diff = float("inf")
    best_split = None

    for combo in itertools.combinations(players, 5):
        team1 = list(combo)
        team2 = [p for p in players if p not in team1]

        mmr1 = sum(get_player(p.id)[0] for p in team1)
        mmr2 = sum(get_player(p.id)[0] for p in team2)

        diff = abs(mmr1 - mmr2)

        if diff < best_diff:
            best_diff = diff
            best_split = (team1, team2)

    return best_split
