#!/usr/bin/env python3
"""One-off backfill: apply XP from rustchain-bounties issue #104 ledger table.

Rules:
- Parse only the markdown table in issue body under Active Entries.
- Skip rows with status=Voided.
- Convert Amount RTC -> tier label:
  <=10 micro, <=50 standard, <=100 major, >100 critical.
- Apply XP using update_xp_tracker_api.py local mode.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class LedgerEntry:
    user: str
    amount: float
    status: str
    pending_id: str
    tx_hash: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--issue-json", default="/tmp/issue104.json")
    p.add_argument("--tracker", default="bounties/XP_TRACKER.md")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def parse_amount(value: str) -> float:
    m = re.search(r"\d+(?:\.\d+)?", value)
    return float(m.group(0)) if m else 0.0


def tier_for_amount(amount: float) -> str:
    if amount <= 10:
        return "micro"
    if amount <= 50:
        return "standard"
    if amount <= 100:
        return "major"
    return "critical"


def parse_ledger_table(body: str) -> List[LedgerEntry]:
    lines = body.splitlines()
    out: List[LedgerEntry] = []

    in_table = False
    for line in lines:
        if line.strip().startswith("| Date (UTC) | Bounty Ref | GitHub User"):
            in_table = True
            continue
        if not in_table:
            continue
        if line.strip().startswith("|---"):
            continue
        if not line.strip().startswith("|"):
            if out:
                break
            continue

        cells = [c.strip() for c in line.strip().split("|")[1:-1]]
        if len(cells) < 9:
            continue

        user_cell = cells[2]
        if not user_cell.startswith("@"):
            continue

        user = user_cell.lstrip("@").strip()
        amount = parse_amount(cells[4])
        status = cells[5].strip().lower()
        pending_id = cells[6].strip().strip("`")
        tx_hash = cells[7].strip().strip("`")

        out.append(
            LedgerEntry(
                user=user,
                amount=amount,
                status=status,
                pending_id=pending_id,
                tx_hash=tx_hash,
            )
        )

    # Dedupe by pending id when available.
    dedup = {}
    for entry in out:
        key = entry.pending_id or entry.tx_hash or f"{entry.user}:{entry.amount}:{entry.status}"
        dedup[key] = entry

    return list(dedup.values())


def apply_xp(entry: LedgerEntry, tracker: str, dry_run: bool) -> None:
    if "voided" in entry.status:
        return

    tier = tier_for_amount(entry.amount)
    labels = f"{tier},ledger"

    cmd = [
        "python3",
        ".github/scripts/update_xp_tracker_api.py",
        "--actor",
        entry.user,
        "--event-type",
        "workflow_dispatch",
        "--event-action",
        "ledger-backfill",
        "--issue-number",
        "104",
        "--labels",
        labels,
        "--pr-merged",
        "false",
        "--local-file",
        tracker,
    ]

    if dry_run:
        print("DRY", " ".join(cmd))
        return

    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


def ensure_maintainer_row(tracker: str, dry_run: bool) -> None:
    text = Path(tracker).read_text(encoding="utf-8")
    if "| @Scottcjn |" in text:
        return

    cmd = [
        "python3",
        ".github/scripts/update_xp_tracker_api.py",
        "--actor",
        "Scottcjn",
        "--event-type",
        "pull_request",
        "--event-action",
        "closed",
        "--issue-number",
        "105",
        "--labels",
        "maintainer",
        "--pr-merged",
        "true",
        "--local-file",
        tracker,
    ]

    if dry_run:
        print("DRY", " ".join(cmd))
        return

    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


def main() -> None:
    args = parse_args()
    data = json.loads(Path(args.issue_json).read_text(encoding="utf-8"))

    entries = parse_ledger_table(data.get("body", ""))
    ensure_maintainer_row(args.tracker, args.dry_run)

    applied = 0
    skipped = 0
    for entry in entries:
        if "voided" in entry.status:
            skipped += 1
            continue
        apply_xp(entry, args.tracker, args.dry_run)
        applied += 1

    print(json.dumps({"entries": len(entries), "applied": applied, "skipped": skipped}, indent=2))


if __name__ == "__main__":
    main()
