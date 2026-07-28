from polis.society.polity import VoteModel
from tests.unit.society.test_vote_model import _model


def test_vote_fit_is_deterministic_and_reports_holdout_quality() -> None:
    first, _candidacy = _model()
    second, _candidacy = _model()
    rows = tuple(
        (
            f"ag_{index:02}",
            "ca_one" if index < 8 else "ca_two",
            {
                "ca_one": {
                    "congruence": 1.0,
                    "self_interest": 0.0,
                    "social": 0.0,
                    "media": 0.0,
                    "party_id": 0.0,
                    "incumbency": 0.0,
                },
                "ca_two": {
                    "congruence": -1.0,
                    "self_interest": 0.0,
                    "social": 0.0,
                    "media": 0.0,
                    "party_id": 0.0,
                    "incumbency": 0.0,
                },
            },
        )
        for index in range(10)
    )

    result = first.fit(rows, "el_one")

    assert result == second.fit(rows, "el_one")
    assert tuple(result.omega) == VoteModel.FEATURES
    assert result.n_deliberate == 10
    assert result.holdout_accuracy == 0.0
    assert not result.usable
