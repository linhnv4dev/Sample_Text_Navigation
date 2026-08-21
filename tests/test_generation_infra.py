"""Test cho hạ tầng mới của phase 6: templates.load_lexicon/index_templates,
sampling.resolve_profiles/plan_to_doc/plan_from_doc, sampling._is_speakable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from navtext.sampling import _is_speakable, plan_from_doc, plan_to_doc, resolve_profiles
from navtext.schema import Category, Cell, LengthClass, Register, SentenceType
from navtext.templates import Lexicon, TemplateError, index_templates, load_lexicon, load_templates

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_TEMPLATES_DIR = REPO_ROOT / "data" / "templates"
DISTRIBUTION_CONFIG = REPO_ROOT / "config" / "distribution.yaml"


def _config() -> dict:
    return yaml.safe_load(DISTRIBUTION_CONFIG.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# load_lexicon
# ---------------------------------------------------------------------------


def test_load_lexicon_real_bank() -> None:
    lex = load_lexicon(REAL_TEMPLATES_DIR)
    assert isinstance(lex, Lexicon)
    for register in Register:
        assert register in lex.openers
        assert register in lex.closers
        assert "" not in [o.text for o in lex.openers[register]]
        assert "" not in lex.closers[register]
        assert len(lex.openers[register]) > 0
        assert len(lex.closers[register]) > 0
        assert all(o.text and o.heads for o in lex.openers[register])
    assert lex.fillers
    assert all(f.text and f.heads for f in lex.fillers)
    for key in ("province", "district", "ward", "street"):
        assert key in lex.admin_prefixes
        assert "" not in lex.admin_prefixes[key]


def test_load_templates_still_works_with_shared_lexicon_loader() -> None:
    templates = load_templates(REAL_TEMPLATES_DIR)
    assert len(templates) > 0


# ---------------------------------------------------------------------------
# resolve_profiles
# ---------------------------------------------------------------------------


def test_resolve_profiles_real_config() -> None:
    config = _config()
    profiles = resolve_profiles(config)
    assert set(profiles) == {p["name"] for p in config["variation_profiles"]}
    reg, length = profiles["casual_short"]
    assert reg == Register.CASUAL
    assert length == LengthClass.SHORT


# ---------------------------------------------------------------------------
# index_templates
# ---------------------------------------------------------------------------


def test_index_templates_real_bank_meets_min_per_cell() -> None:
    config = _config()
    templates = load_templates(REAL_TEMPLATES_DIR)
    profiles = resolve_profiles(config)
    min_per_cell = int(config["min_templates_per_cell"])

    index = index_templates(templates, profiles, min_per_cell)

    assert len(index) == len(list(SentenceType)) * len(profiles)
    for key, matched in index.items():
        assert len(matched) >= min_per_cell
        sentence_type, profile_name = key
        assert all(t.sentence_type == sentence_type for t in matched)
        want_register, want_length = profiles[profile_name]
        assert all(t.register == want_register and t.length_class == want_length for t in matched)


def test_index_templates_raises_when_under_threshold() -> None:
    templates = load_templates(REAL_TEMPLATES_DIR)
    config = _config()
    profiles = resolve_profiles(config)
    with pytest.raises(TemplateError):
        index_templates(templates, profiles, min_templates_per_cell=10_000)


# ---------------------------------------------------------------------------
# _is_speakable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Nguyễn Văn Linh", True),
        ("BIDV", True),
        ("WinMart", True),
        ("GO!", True),
        ("丸亀製麺", False),
        ("맛찬들 소금구이", False),
        ("", False),
        ("   ", False),
    ],
)
def test_is_speakable(name: str, expected: bool) -> None:
    assert _is_speakable(name) is expected


# ---------------------------------------------------------------------------
# plan_to_doc / plan_from_doc round-trip
# ---------------------------------------------------------------------------


def test_plan_doc_round_trip() -> None:
    plan = [
        Cell(
            province_id="01",
            category=Category.STREET,
            sentence_type=SentenceType.NAV_COMMAND,
            variation_profile="casual_short",
            target_count=42,
        ),
        Cell(
            province_id="02",
            category=Category.ADMIN_UNIT,
            sentence_type=SentenceType.SEARCH,
            variation_profile="formal_medium",
            target_count=7,
        ),
    ]
    doc = plan_to_doc(
        plan,
        run_id="r1",
        total=49,
        global_seed=20260815,
        config_hash="abc",
        master_data_hash="def",
        generated_at="2026-08-15T12:00:00+00:00",
    )
    assert doc["schema_version"] == 1
    assert doc["total"] == 49
    assert len(doc["cells"]) == 2

    restored = plan_from_doc(doc)
    assert restored == plan


def test_plan_from_doc_rejects_unknown_schema_version() -> None:
    from navtext.sampling import PlanError

    with pytest.raises(PlanError):
        plan_from_doc({"schema_version": 999, "cells": []})
