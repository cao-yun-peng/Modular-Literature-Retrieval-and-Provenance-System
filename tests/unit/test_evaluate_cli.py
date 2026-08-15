"""Unit tests for evaluation CLI argument helpers."""

import argparse

import pytest

from scripts.evaluate import parse_metric_threshold


def test_parse_metric_threshold() -> None:
    assert parse_metric_threshold("recall_at_k=0.8") == ("recall_at_k", 0.8)


@pytest.mark.parametrize(
    "value",
    ["recall_at_k", "=0.8", "mrr=invalid", "mrr=-0.1", "mrr=1.1"],
)
def test_parse_metric_threshold_rejects_invalid_values(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_metric_threshold(value)
