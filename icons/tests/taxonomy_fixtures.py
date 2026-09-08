"""Synthetic fresh-contract observations; no historical production response data."""

DESCRIPTIONS = {
    "praying": "The central figure is praying, hands joined in prayer.",
    "sharing_food": "A seated figure is sharing food with the person beside them.",
    "giving_alms": "A standing figure is giving coins to a seated beggar.",
    "washing_feet": "A kneeling figure is washing the feet of a seated person beside a basin.",
    "comforting": "One figure is comforting another, holding their shoulders in an embrace.",
    "shared_supper": "A shared supper: several figures sit together around a table with plates.",
    "bread_and_cup": "Bread and cup stand on the table between the diners.",
    "returning_son": "A returning son approaches his father, carrying a travel bag.",
    "father_embracing_son": "A father holds his son in both arms, with the younger figure leaning into the embrace.",
}


def fresh_observations(names, activities):
    observations = [
        dict(
            id=f"n{i}",
            kind="inscription",
            text="The inscription reads the literal name.",
            region="upper inscription",
            readable=True,
            uncertain=False,
            code="unknown",
            literal_spans=[name],
        )
        for i, name in enumerate(names)
    ]
    observations += [
        dict(
            id=f"a{i}",
            kind="activity",
            text=DESCRIPTIONS[value],
            region="center action",
            readable=False,
            uncertain=False,
            code=value,
            literal_spans=[],
        )
        for i, value in enumerate(activities)
    ]
    return observations
