from config import K_FACTOR

def expected(mmr_a, mmr_b):
    return 1 / (1 + 10 ** ((mmr_b - mmr_a) / 400))

def calculate_elo(mmr_a, mmr_b, result):
    exp = expected(mmr_a, mmr_b)
    return int(K_FACTOR * (result - exp))
