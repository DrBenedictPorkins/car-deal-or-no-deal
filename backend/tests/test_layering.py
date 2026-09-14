"""Structural invariants.

These are the rules that are easy to violate by accident and expensive to discover
late, so they are asserted rather than documented and hoped for.
"""

from __future__ import annotations

import ast
from pathlib import Path

from sqlalchemy import Float, Integer, Numeric

from app.models import Base

APP = Path(__file__).resolve().parents[1] / "app"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_no_domain_service_imports_an_llm():
    """The LLM is a sensor, never the record. Only enrichment/ may talk to a model."""
    offenders = []
    for path in sorted((APP / "services").glob("*.py")):
        if any(name.startswith("app.llm") for name in _imports(path)):
            offenders.append(path.name)
    assert offenders == [], f"LLM imported by domain service(s): {offenders}"


def test_no_model_or_service_imports_a_vendor_sdk():
    """Business logic must not know which vendor exists."""
    vendors = {"anthropic", "openai", "ollama", "google.generativeai", "cohere"}
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        if path.parts[-2] == "llm":
            continue  # adapters are allowed to, by definition
        hits = {name.split(".")[0] for name in _imports(path)} & vendors
        if hits:
            offenders.append((str(path.relative_to(APP)), sorted(hits)))
    assert offenders == [], f"vendor SDK referenced outside app/llm: {offenders}"


def test_services_do_not_import_routers():
    offenders = []
    for path in sorted((APP / "services").glob("*.py")):
        if any(name.startswith("app.api") for name in _imports(path)):
            offenders.append(path.name)
    assert offenders == [], f"service(s) importing routers: {offenders}"


def test_every_money_column_is_an_integer_of_cents():
    """No floats in the money path — not in storage, not anywhere."""
    offenders = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if not column.name.endswith("_cents"):
                continue
            if isinstance(column.type, Float | Numeric):
                offenders.append(f"{table.name}.{column.name}")
    assert offenders == [], f"non-integer money column(s): {offenders}"


MONEY_TOKENS = {"price", "fee", "fees", "cost", "msrp", "otd", "tax", "discount"}
RATE_TOKENS = {"rate", "apr"}


def test_money_columns_are_named_consistently():
    """A numeric price column that doesn't say _cents will be read as dollars one day."""
    suspicious = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            name = column.name
            if name.endswith("_cents") or name.endswith("_bp"):
                continue
            if not isinstance(column.type, Integer | Float | Numeric):
                continue
            if set(name.split("_")) & MONEY_TOKENS:
                suspicious.append(f"{table.name}.{name}")
    assert suspicious == [], f"money-ish column(s) without a _cents suffix: {suspicious}"


def test_percentages_are_stored_as_basis_points():
    offenders = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if column.name.endswith("_bp"):
                continue
            if not isinstance(column.type, Integer | Float | Numeric):
                continue
            if set(column.name.split("_")) & RATE_TOKENS:
                offenders.append(f"{table.name}.{column.name}")
    assert offenders == [], f"rate column(s) not in basis points: {offenders}"


def test_offer_has_no_stored_add_ons_total():
    """A total that can disagree with its parts is the failure this system prevents."""
    columns = {c.name for c in Base.metadata.tables["offer"].columns}
    assert "add_ons_total_cents" not in columns
    assert "dealer_controlled_cents" not in columns
    assert "otd_cents" not in columns


def test_dealer_stores_no_derived_negotiation_state():
    """Derived values are recomputed on read, never cached into a second source."""
    columns = {c.name for c in Base.metadata.tables["dealer"].columns}
    for derived in ("owes_response", "idle_days", "next_action", "friction_score", "best_otd"):
        assert derived not in columns
