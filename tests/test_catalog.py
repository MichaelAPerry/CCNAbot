"""The competency grid is the system's set of primary keys. If these change,
every stored observation and every signed report is affected."""

import pytest

from ccnabot.domain.competency import Modality


def test_grid_shape(catalog):
    assert len(catalog) == 91, "live tasks in CIP 11.0901"
    assert len(catalog.all_including_reserved) == 96
    assert len(catalog.sections) == 11


def test_reserved_tasks_are_not_addressable(catalog):
    reserved = {"112", "215", "303", "306", "805"}
    for task_id in reserved:
        assert catalog.knows(task_id), "reserved ids still occupy their slot"
        assert catalog.is_reserved(task_id)
        # ...but they are not addressable, so evidence cannot be filed
        # against them and then quietly lost.
        assert task_id not in catalog
        assert catalog.get(task_id) is None
        with pytest.raises(KeyError, match="RESERVED"):
            catalog[task_id]


def test_805_is_reserved_and_806_is_the_real_task(catalog):
    """The year plan cites 805 twice; 806 is what it means. Guarding this
    because a silent renumber would drop a competency from every record."""
    assert catalog.get("805") is None
    assert catalog["806"].title.startswith("Implement remote management")


def test_every_live_task_has_a_modality(catalog):
    for c in catalog:
        assert c.modality, f"{c.id} has no modality"
        assert c.title


def test_modality_distribution(catalog):
    """The shape of this grid is why the agent cannot just watch the rack."""
    counts = {m: len(catalog.with_modality(m)) for m in Modality}
    assert counts[Modality.RECALL] > counts[Modality.RACK] * 3, (
        "recall dominates the grid; the retention layer is not a side feature"
    )
    assert counts[Modality.ATTEST] >= 20, "physical tasks need an instructor path"


def test_instructor_only_tasks_are_identified(catalog):
    needs = {c.id for c in catalog.requiring_instructor()}
    # Crimping to 568A/568B and wearing PPE cannot be observed from a console.
    assert {"604", "102", "111", "214"} <= needs
    # ...whereas switch features plainly can be.
    assert "703" not in needs


def test_lookup_of_unknown_task_is_explicit(catalog):
    with pytest.raises(KeyError, match="no such task"):
        catalog["999"]
