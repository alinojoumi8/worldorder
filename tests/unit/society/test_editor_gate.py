from polis.society.media.news import Claim, Draft, EditorGate, Outlet


def draft(**changes: object) -> Draft:
    values: dict[str, object] = {
        "outlet_id": "ol_one",
        "reporter_id": "ag_reporter",
        "headline": "Headline",
        "body": "Body",
        "source_event_seqs": (1,),
        "claims": (
            Claim(
                "cl_one",
                "Claim",
                "fm_one",
                "firm.solvent",
                True,
                1,
                (1,),
            ),
        ),
    }
    values.update(changes)
    return Draft(**values)  # type: ignore[arg-type]


def test_editor_gate_all_spike_reasons() -> None:
    gate = EditorGate()
    rigorous = Outlet("ol_one", "One", None, -0.8, 0.9, 0, None)
    loose = Outlet("ol_one", "One", None, 0.0, 0.2, 0, None)
    assert gate.spike_reason(draft(claims=()), rigorous) == "thin_sourcing"
    assert gate.spike_reason(draft(stance_value=0.9), rigorous) == "slant_mismatch"
    assert gate.spike_reason(draft(legal_risk=True), rigorous) == "legal_risk"
    assert gate.spike_reason(draft(legal_risk=True), loose) is None
    assert gate.spike_reason(draft(over_budget=True), rigorous) == "budget"
    assert gate.review(draft(), rigorous, 1) == "publish"
    thin = draft(claims=())
    assert gate.review(thin, rigorous, 1) == "rewrite"
    assert gate.review(thin, rigorous, 2) == "spike"
