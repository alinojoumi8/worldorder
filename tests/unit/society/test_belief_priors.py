from polis.config.settings import BeliefSettings
from polis.society.beliefs import POLICY_PROPOSITIONS, Belief
from tests.unit.society.test_belief_kernel import engine


def test_birth_priors_are_deterministic_and_never_inherit_facts() -> None:
    beliefs, repo, _ = engine(belief_cfg=BeliefSettings(sigma_belief=0.0, heritability_beliefs=1.0))
    for parent, value in (("ag_a", -0.5), ("ag_b", 0.5)):
        for proposition in (*POLICY_PROPOSITIONS, "trust.generalised"):
            repo.put(
                Belief(
                    parent,
                    proposition,
                    0.5 if proposition == "trust.generalised" else value,
                    0.8,
                    "inherited",
                    None,
                    0,
                )
            )
    first = beliefs.priors_at_birth("ag_child", "ag_a", "ag_b")
    second = beliefs.priors_at_birth("ag_child", "ag_a", "ag_b")
    assert first == second
    assert all(not proposition.startswith("fact.") for proposition, _, _ in first)
    assert len(first) == len(POLICY_PROPOSITIONS) + 1
    assert all(confidence == 0.4 for _, _, confidence in first)


def test_migrant_priors_do_not_use_entity_facts() -> None:
    beliefs, _, _ = engine()
    first = beliefs.priors_for_migrant("ag_new", {"policy.tax.progressivity": 0.2})
    replay = beliefs.priors_for_migrant("ag_new", {"policy.tax.progressivity": 0.2})
    second_agent = beliefs.priors_for_migrant("ag_other", {"policy.tax.progressivity": 0.2})

    assert first == replay
    assert first != second_agent
    assert all(not proposition.startswith("fact.") for proposition, _, _ in first)
    assert all(confidence <= beliefs.cfg.confidence_dilution for _, _, confidence in first)
