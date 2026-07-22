"""Deterministic MoSCoW prioritisation — a criticality-based proposal (NOT modal-based, since the
extractor normalises everything to 'shall'). The product owner confirms at the gate."""

from __future__ import annotations

from rga.agents.prioritize import prioritize
from rga.models import Priority, RType


def test_compliance_and_security_and_payment_are_must():
    assert prioritize("The system shall comply with the GDPR erasure obligation.", RType.constraint)[0] == Priority.must
    assert prioritize("Card data shall be encrypted in transit.", RType.non_functional)[0] == Priority.must
    assert prioritize("The system shall process card payments via the PSP.", RType.functional)[0] == Priority.must
    # criticality wins even over a soft modal
    assert prioritize("The system may log security audit events.", RType.functional)[0] == Priority.must


def test_optionality_cue_is_could():
    assert prioritize("A product wishlist is a nice-to-have feature.", RType.functional)[0] == Priority.could
    assert prioritize("The system shall optionally support dark mode if feasible.", RType.functional)[0] == Priority.could


def test_explicit_optionality_beats_must_domain_keyword():
    """L2: an explicit optional cue wins over a must-domain keyword ('audit')."""
    assert prioritize("The system may optionally provide an audit export if feasible.",
                      RType.functional)[0] == Priority.could


def test_inferred_is_could():
    assert prioritize("The system provides product search.", RType.functional, inferred=True)[0] == Priority.could


def test_soft_modal_without_hard_modal_is_could():
    assert prioritize("The system may support a dark mode theme.", RType.functional)[0] == Priority.could


def test_business_and_constraint_default_must_functional_defaults_should():
    # firm policy -> must
    assert prioritize("Orders progress through the defined state machine.", RType.business)[0] == Priority.must
    # ordinary, NON-launch-critical functional 'shall' is NOT auto-must (the fix for must=220): 'should'
    assert prioritize("The system shall display an About Us page.", RType.functional)[0] == Priority.should
    assert prioritize("The system shall let a user set a profile avatar.", RType.functional)[0] == Priority.should


def test_launch_critical_commerce_basics_are_must():
    """Part I: storefront basics cannot ship as 'could/should' — they are High (must)."""
    for s in ("The system shall let a customer add an item to the cart.",
              "The system shall provide a persistent shopping cart.",
              "The system shall let the customer complete checkout.",
              "The system shall reserve stock when an order is placed.",
              "The system shall let customers browse products by category."):
        assert prioritize(s, RType.functional)[0] == Priority.must, s


def test_returns_reason_string():
    prio, reason = prioritize("The system shall display an About Us page.", RType.functional)
    assert prio == Priority.should and isinstance(reason, str) and reason
