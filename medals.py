def get_medal(mmr):
    tiers = [
        (800, "Herald"),
        (1000, "Guardian"),
        (1200, "Crusader"),
        (1500, "Archon"),
        (1800, "Legend"),
        (2200, "Ancient"),
        (2600, "Divine"),
    ]
    for value, name in tiers:
        if mmr < value:
            return name
    return "Immortal"
