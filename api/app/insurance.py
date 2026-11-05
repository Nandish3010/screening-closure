"""The insurance scheme of a case and what it means for a referral (ADR-056).

Pure vocabulary and wording; nothing here reads a file, a clock or a store. The scheme codes are generic and the network
data that goes with them (config/providers.yaml `referral.networks`) is SYNTHETIC. Only `thiqa` is a real scheme's name:
the repository already cites it with sources (docs/commercial/buyer-map.md); `enhanced` and `basic` are generic tiers,
not any insurer's products.

The scheme is an optional field of the genesis event (`insurance_scheme`). It is never a hold condition: a case with no
scheme, or with `unknown`, is routed exactly as it was before this field existed.
"""

from __future__ import annotations

SCHEMES = ("thiqa", "enhanced", "basic")  # the schemes a network can accept
UNKNOWN = "unknown"
SCHEME_CODES = (*SCHEMES, UNKNOWN)  # what the genesis payload may carry
LABELS = {"thiqa": "Thiqa", "enhanced": "Enhanced", "basic": "Basic", UNKNOWN: "Unknown"}

IN_NETWORK = "in_network"
OUT_OF_NETWORK = "out_of_network"
NETWORKS = (IN_NETWORK, OUT_OF_NETWORK)


def is_known(scheme: object) -> bool:
    """True for a scheme a network can accept; False for None, `unknown` and anything else."""
    return scheme in SCHEMES


def label(scheme: str | None) -> str:
    return LABELS.get(scheme or "", LABELS[UNKNOWN])


def reason(scheme: str, network: str | None, auto: bool) -> str | None:
    """The plain-words answer to "why this provider?" for one referral request. None when the scheme did not take part in
    the choice (no network recorded). `auto` is a reroute by the automation; otherwise a person chose the unit."""
    if network is None:
        return None
    plan = f"the patient's {label(scheme)} plan"
    if network == IN_NETWORK:
        if auto:
            return f"Chosen because it accepts {plan} and is the first such unit in the programme's referral order."
        return f"This unit accepts {plan}."
    if auto:
        return (f"Out of network: no remaining unit for this service accepts {plan}, so the referral goes to the best "
                "available unit outside it. A coordinator should check the cover with the patient. The referral is not blocked.")
    return (f"Out of network: this unit does not accept {plan}. A coordinator should check the cover with the patient "
            "or choose a unit that does. The referral is not blocked.")
