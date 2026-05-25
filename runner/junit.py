"""Parse a pytest junit XML report into TestResult records."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ParsedTest:
    test_name: str
    outcome: str  # passed|failed|skipped|error
    duration_ms: int
    error_excerpt: str | None = None


def parse_junit(xml_path: Path) -> list[ParsedTest]:
    if not xml_path.exists():
        return []

    tree = ET.parse(xml_path)
    root = tree.getroot()

    suites = (
        root.findall(".//testsuite")
        if root.tag != "testsuite"
        else [root]
    )

    parsed: list[ParsedTest] = []
    for suite in suites:
        for case in suite.findall("testcase"):
            classname = case.get("classname", "")
            name = case.get("name", "")
            test_name = f"{classname}::{name}" if classname else name

            try:
                duration_ms = int(float(case.get("time", "0")) * 1000)
            except ValueError:
                duration_ms = 0

            failure = case.find("failure")
            error = case.find("error")
            skipped = case.find("skipped")

            if failure is not None:
                outcome = "failed"
                excerpt = _truncate(
                    failure.get("message", "") or (failure.text or "")
                )
            elif error is not None:
                outcome = "error"
                excerpt = _truncate(
                    error.get("message", "") or (error.text or "")
                )
            elif skipped is not None:
                outcome = "skipped"
                excerpt = _truncate(
                    skipped.get("message", "") or (skipped.text or "")
                )
            else:
                outcome = "passed"
                excerpt = None

            parsed.append(
                ParsedTest(
                    test_name=test_name,
                    outcome=outcome,
                    duration_ms=duration_ms,
                    error_excerpt=excerpt,
                )
            )

    return parsed


def _truncate(text: str, limit: int = 4000) -> str | None:
    if not text:
        return None
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"
