#!/usr/bin/env python3
"""Auto-triage community bounty claims and update ledger issue block.

This script is designed for GitHub Actions. It checks claim comments on
configured bounty issues and marks each recent claim as:
- `eligible`
- `needs-action`

It does not queue payouts directly. It generates an audit-friendly report that
maintainers can use to process payments quickly and consistently.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

try:
    from scripts.sybil_risk_scorer import ClaimInput as RiskClaimInput
    from scripts.sybil_risk_scorer import extract_links, score_claims
except ImportError:  # pragma: no cover - direct script execution fallback
    from sybil_risk_scorer import ClaimInput as RiskClaimInput
    from sybil_risk_scorer import extract_links, score_claims


DEFAULT_TARGETS = [
    {
        "owner": "Scottcjn",
        "repo": "rustchain-bounties",
        "issue": 87,
        "min_account_age_days": 30,
        "required_stars": ["Rustchain", "bottube"],
        "require_wallet": True,
        "require_bottube_username": False,
        "require_proof_link": False,
        "name": "Community Support",
    },
    {
        "owner": "Scottcjn",
        "repo": "Rustchain",
        "issue": 47,
        "min_account_age_days": 30,
        "required_stars": ["Rustchain"],
        # Bounty allows either a RustChain wallet name OR a BoTTube username.
        # Treat either as a valid payout target.
        "require_wallet": False,
        "require_bottube_username": False,
        "require_payout_target": True,
        "require_proof_link": False,
        "name": "Rustchain Star",
    },
    {
        "owner": "Scottcjn",
        "repo": "bottube",
        "issue": 74,
        "min_account_age_days": 30,
        "required_stars": ["bottube"],
        "require_wallet": False,
        "require_bottube_username": True,
        "require_proof_link": False,
        "name": "BoTTube Star+Join",
    },
    {
        "owner": "Scottcjn",
        "repo": "rustchain-bounties",
        "issue": 103,
        "min_account_age_days": 30,
        "required_stars": [],
        "require_wallet": True,
        "require_bottube_username": True,
        "require_proof_link": True,
        "name": "X + BoTTube Social",
    },
    {
        "owner": "Scottcjn",
        "repo": "rustchain-bounties",
        "issue": 374,
        "min_account_age_days": 30,
        "required_stars": [],
        "require_wallet": True,
        "require_bottube_username": False,
        "require_proof_link": True,
        "name": "First Attest Bonus",
    },
    {
        "owner": "Scottcjn",
        "repo": "rustchain-bounties",
        "issue": 157,
        "min_account_age_days": 30,
        "required_stars": ["beacon-skill"],
        "require_wallet": True,
        "require_bottube_username": False,
        "require_proof_link": True,
        "name": "Beacon Star + Share",
    },
    {
        "owner": "Scottcjn",
        "repo": "rustchain-bounties",
        "issue": 158,
        "min_account_age_days": 30,
        "required_stars": [],
        "require_wallet": True,
        "require_bottube_username": False,
        "require_proof_link": True,
        "name": "Beacon Integration",
    },
    {
        "owner": "Scottcjn",
        "repo": "bottube",
        "issue": 122,
        "min_account_age_days": 30,
        "required_stars": ["bottube"],
        "require_wallet": True,
        "require_bottube_username": False,
        "require_proof_link": True,
        "name": "BoTTube Star + Share Why",
    },
    {
        "owner": "Scottcjn",
        "repo": "rustchain-bounties",
        "issue": 377,
        "min_account_age_days": 30,
        "required_stars": [],
        "require_wallet": True,
        "require_bottube_username": False,
        "require_proof_link": True,
        "name": "Beacon Mechanism Falsification",
    },
]

MARKER_START = "<!-- auto-triage-report:start -->"
MARKER_END = "<!-- auto-triage-report:end -->"
PR_URL_RE = re.compile(
    r"https?://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/pull/(?P<number>\d+)",
    re.IGNORECASE,
)
PR_REF_RE = re.compile(
    r"(?i)\b(?:draft\s+)?(?:pr|pull\s+request)\s*#(?P<number>\d+)\b"
)


def _env(name: str, default: Optional[str] = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Missing required env: {name}")
    return value


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _gh_request(
    method: str,
    path: str,
    token: str,
    data: Optional[Dict[str, Any]] = None,
    accept: str = "application/vnd.github+json",
) -> Any:
    base = "https://api.github.com"
    url = path if path.startswith("http") else f"{base}{path}"
    payload = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method=method.upper())
    req.add_header("Accept", accept)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", "elyan-auto-triage")
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _gh_paginated(
    path: str,
    token: str,
    accept: str = "application/vnd.github+json",
) -> List[Dict[str, Any]]:
    page = 1
    out: List[Dict[str, Any]] = []
    while True:
        sep = "&" if "?" in path else "?"
        p = f"{path}{sep}per_page=100&page={page}"
        chunk = _gh_request("GET", p, token, accept=accept)
        if not isinstance(chunk, list) or not chunk:
            break
        out.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
    return out


def _extract_wallet(body: str) -> Optional[str]:
    # Strip minimal markdown that commonly wraps labels like **RTC Wallet:**,
    # without corrupting valid underscores in wallet names (e.g. abdul_rtc_01).
    body = re.sub(r"[`*]", "", body)

    stop = {"wallet", "address", "miner_id", "please", "thanks", "thankyou"}
    found: Optional[str] = None
    expect_next = False
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue

        # Handle "Wallet:" on one line and the value on the next.
        if expect_next:
            expect_next = False
            if re.fullmatch(r"[A-Za-z0-9_\-]{4,80}", s) and s.lower() not in stop:
                if re.search(r"[0-9_\-]", s) or s.upper().startswith("RTC") or len(s) >= 6:
                    found = s
                    continue

        # Common non-English label (Chinese): "钱包地址： <wallet>" or value on next line.
        m = re.search(r"钱包(?:地址)?\s*[:：\-]\s*([A-Za-z0-9_\-]{4,80})\b", s)
        if m:
            val = m.group(1).strip()
            if val.lower() not in stop:
                found = val
                continue
        if re.search(r"钱包(?:地址)?\s*[:：\-]\s*$", s):
            expect_next = True
            continue

        # English label with value on next line.
        if re.search(r"(?i)\b(?:rtc\s*)?(?:wallet|miner[_\-\s]?id|address)\b.*[:：\-]\s*$", s):
            expect_next = True
            continue

        # English label + value on same line (also allows "Payout target miner_id: X").
        m = re.search(
            r"(?i)\b(?:payout\s*target\s*)?"
            r"(?:rtc\s*)?"
            r"(wallet|miner[_\-\s]?id|address)\s*"
            r"(?:\((?:miner_?id|id|address)\))?\s*[:：\-]\s*"
            r"([A-Za-z0-9_\-]{4,80})\b",
            s,
        )
        if not m:
            continue
        val = m.group(2).strip()
        if val.lower() in stop:
            continue
        # Heuristic: avoid capturing short plain words after "wallet:".
        if not re.search(r"[0-9_\-]", val) and not val.upper().startswith("RTC") and len(val) < 6:
            continue
        found = val

    return found


def _extract_bottube_user(body: str) -> Optional[str]:
    # Strip minimal markdown without corrupting valid underscores in usernames.
    body = re.sub(r"[`*]", "", body)
    patterns = [
        # Prefer extracting from profile URLs if present.
        r"https?://(?:www\.)?bottube\.ai/@([A-Za-z0-9_-]{2,64})",
        r"https?://(?:www\.)?bottube\.ai/agent/([A-Za-z0-9_-]{2,64})",
        # Explicit label on its own line.
        r"(?im)^\s*bottube(?:\s*(?:username|user|account))?\s*[:：\-]\s*(?!https?\b)([A-Za-z0-9_-]{2,64})\s*$",
    ]
    for pat in patterns:
        matches = list(re.finditer(pat, body))
        if matches:
            return matches[-1].group(1).strip()
    return None


def _has_proof_link(body: str) -> bool:
    return bool(re.search(r"https?://", body))


def _wallet_looks_external(wallet: str) -> bool:
    # Heuristic: very long base58/base62 tokens are usually external chain
    # addresses, not RTC wallet names used in these bounties.
    if re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{28,64}", wallet):
        return True
    if re.fullmatch(r"[A-Za-z0-9]{30,64}", wallet):
        return True
    return False


def _looks_like_claim(body: str) -> bool:
    text = body.lower()
    tokens = [
        "claim",
        "starred",
        "wallet",
        "proof",
        "bounty",
        "rtc",
        "payout",
        "submission",
        "submit",
        "pr",
        "pull request",
        "demo",
    ]
    return any(t in text for t in tokens)


def _status_label(blockers: List[str]) -> str:
    return "eligible" if not blockers else "needs-action"


def _hours_since(ts: Optional[str], now: datetime) -> Optional[float]:
    if not ts:
        return None
    return max(0.0, (now - _parse_iso(ts)).total_seconds() / 3600.0)


def _hours_between(start: Optional[str], end: Optional[str]) -> Optional[float]:
    if not start or not end:
        return None
    return max(0.0, (_parse_iso(end) - _parse_iso(start)).total_seconds() / 3600.0)


def _format_hours(value: Optional[float]) -> str:
    if value is None:
        return ""
    return str(int(round(value)))


def _max_timestamp(*values: Optional[str]) -> Optional[str]:
    known = [value for value in values if value]
    if not known:
        return None
    return max(known, key=_parse_iso)


@dataclass(frozen=True)
class LinkedPullRequest:
    owner: str
    repo: str
    number: int
    url: str
    state: str
    draft: Optional[bool]
    created_at: str
    updated_at: str
    author: str

    @property
    def repo_ref(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def short_ref(self) -> str:
        return f"{self.repo_ref}#{self.number}"


@dataclass
class ClaimSession:
    user: str
    issue_ref: str
    first_claim_url: str
    created_at: str
    first_claim_body: str
    latest_update_url: str
    latest_update_at: str
    latest_update_body: str
    wallet: Optional[str] = None
    bottube_user: Optional[str] = None
    proof_links: Set[str] = field(default_factory=set)
    pr_refs: Set[Tuple[str, str, int]] = field(default_factory=set)


@dataclass
class ClaimResult:
    claim_id: str
    user: str
    issue_ref: str
    comment_url: str
    created_at: str
    account_age_days: Optional[int]
    wallet: Optional[str]
    bottube_user: Optional[str]
    blockers: List[str]
    proof_links: List[str] = field(default_factory=list)
    body: str = ""
    latest_update_at: str = ""
    claim_age_hours: Optional[float] = None
    silence_hours: Optional[float] = None
    linked_pr_ref: Optional[str] = None
    linked_pr_url: Optional[str] = None
    linked_pr_state: Optional[str] = None
    linked_pr_draft: Optional[bool] = None
    linked_pr_created_at: Optional[str] = None
    linked_pr_updated_at: Optional[str] = None
    risk_score: int = 0
    risk_level: str = "low"
    risk_reasons: List[str] = field(default_factory=list)
    action: str = "watch"
    action_reason: str = ""

    @property
    def status(self) -> str:
        return _status_label(self.blockers)


def _extract_pr_refs(body: str, owner: str, repo: str) -> Set[Tuple[str, str, int]]:
    refs: Set[Tuple[str, str, int]] = set()
    for match in PR_URL_RE.finditer(body or ""):
        refs.add(
            (
                match.group("owner"),
                match.group("repo"),
                int(match.group("number")),
            )
        )
    for match in PR_REF_RE.finditer(body or ""):
        refs.add((owner, repo, int(match.group("number"))))
    return refs


def _apply_comment_to_session(
    session: ClaimSession,
    body: str,
    created_at: str,
    comment_url: str,
    owner: str,
    repo: str,
) -> None:
    wallet = _extract_wallet(body)
    if wallet:
        session.wallet = wallet
    bottube_user = _extract_bottube_user(body)
    if bottube_user:
        session.bottube_user = bottube_user
    session.proof_links.update(extract_links(body))
    session.pr_refs.update(_extract_pr_refs(body, owner, repo))
    if _parse_iso(session.latest_update_at) <= _parse_iso(created_at):
        session.latest_update_at = created_at
        session.latest_update_url = comment_url
        session.latest_update_body = body


def _collect_claim_sessions(
    comments: Sequence[Dict[str, Any]],
    owner: str,
    repo: str,
    issue_ref: str,
    ignored_users: Set[str],
) -> List[ClaimSession]:
    sessions: Dict[str, ClaimSession] = {}
    for comment in sorted(comments, key=lambda item: _parse_iso(item.get("created_at") or "1970-01-01T00:00:00Z")):
        user = ((comment.get("user") or {}).get("login") or "").strip()
        if not user or user.lower() in ignored_users:
            continue
        created_at = comment.get("created_at")
        if not created_at:
            continue
        body = comment.get("body") or ""
        url = comment.get("html_url") or ""
        if user not in sessions:
            if not _looks_like_claim(body):
                continue
            session = ClaimSession(
                user=user,
                issue_ref=issue_ref,
                first_claim_url=url,
                created_at=created_at,
                first_claim_body=body,
                latest_update_url=url,
                latest_update_at=created_at,
                latest_update_body=body,
            )
            _apply_comment_to_session(session, body, created_at, url, owner, repo)
            sessions[user] = session
            continue
        _apply_comment_to_session(sessions[user], body, created_at, url, owner, repo)
    return list(sessions.values())


def _parse_pr_html_url(url: str) -> Optional[Tuple[str, str, int]]:
    match = PR_URL_RE.match(url or "")
    if not match:
        return None
    return match.group("owner"), match.group("repo"), int(match.group("number"))


def _load_timeline_pr_refs(
    owner: str,
    repo: str,
    issue: int,
    token: str,
) -> Dict[str, Set[Tuple[str, str, int]]]:
    refs_by_user: Dict[str, Set[Tuple[str, str, int]]] = {}
    try:
        events = _gh_paginated(f"/repos/{owner}/{repo}/issues/{issue}/timeline", token)
    except urllib.error.HTTPError:
        return refs_by_user

    for event in events:
        source = event.get("source") or {}
        source_issue = source.get("issue") or {}
        if not source_issue.get("pull_request"):
            continue
        source_user = ((source_issue.get("user") or {}).get("login") or "").strip().lower()
        if not source_user:
            continue
        parsed = _parse_pr_html_url(source_issue.get("html_url") or "")
        if not parsed:
            continue
        refs_by_user.setdefault(source_user, set()).add(parsed)
    return refs_by_user


def _fetch_linked_pr(
    owner: str,
    repo: str,
    number: int,
    token: str,
    cache: Dict[Tuple[str, str, int], Optional[LinkedPullRequest]],
) -> Optional[LinkedPullRequest]:
    key = (owner, repo, number)
    if key in cache:
        return cache[key]
    try:
        data = _gh_request("GET", f"/repos/{owner}/{repo}/pulls/{number}", token)
    except urllib.error.HTTPError:
        cache[key] = None
        return None
    pr = LinkedPullRequest(
        owner=owner,
        repo=repo,
        number=int(data.get("number") or number),
        url=data.get("html_url") or f"https://github.com/{owner}/{repo}/pull/{number}",
        state=str(data.get("state") or ""),
        draft=data.get("draft"),
        created_at=str(data.get("created_at") or ""),
        updated_at=str(data.get("updated_at") or data.get("created_at") or ""),
        author=((data.get("user") or {}).get("login") or "").strip(),
    )
    cache[key] = pr
    return pr


def _linked_prs_for_session(
    session: ClaimSession,
    timeline_refs: Dict[str, Set[Tuple[str, str, int]]],
    token: str,
    cache: Dict[Tuple[str, str, int], Optional[LinkedPullRequest]],
) -> List[LinkedPullRequest]:
    refs = set(session.pr_refs)
    refs.update(timeline_refs.get(session.user.lower(), set()))
    linked: List[LinkedPullRequest] = []
    for owner, repo, number in sorted(refs):
        pr = _fetch_linked_pr(owner, repo, number, token, cache)
        if pr is None:
            continue
        if pr.author.lower() != session.user.lower():
            continue
        linked.append(pr)
    return linked


def _select_primary_pr(prs: Sequence[LinkedPullRequest]) -> Optional[LinkedPullRequest]:
    if not prs:
        return None

    def sort_key(pr: LinkedPullRequest) -> Tuple[int, int, float]:
        state_rank = 0 if pr.state == "open" and not pr.draft else 1 if pr.state == "open" else 2
        return (state_rank, 0, -_parse_iso(pr.updated_at).timestamp())

    return sorted(prs, key=sort_key)[0]


def _session_last_activity(
    session: ClaimSession,
    primary_pr: Optional[LinkedPullRequest],
) -> str:
    pr_activity = None
    if primary_pr:
        pr_activity = _max_timestamp(primary_pr.updated_at, primary_pr.created_at)
    return _max_timestamp(session.latest_update_at, pr_activity) or session.latest_update_at


def _needs_detail_request(blockers: Sequence[str]) -> bool:
    for blocker in blockers:
        if blocker in {
            "missing_wallet",
            "missing_bottube_username",
            "missing_proof_link",
            "missing_payout_target",
            "wallet_external_format",
        }:
            return True
        if blocker.startswith("missing_star:"):
            return True
    return False


def _derive_action(row: ClaimResult) -> Tuple[str, str]:
    if _needs_detail_request(row.blockers):
        return "request_details", "missing payout details or proof"

    if row.silence_hours is not None and row.silence_hours >= 72:
        return "release_claim", f"idle for {int(round(row.silence_hours))}h"

    if row.claim_age_hours is not None and row.claim_age_hours >= 24 and not row.linked_pr_url:
        return "release_claim", f"no linked PR after {int(round(row.claim_age_hours))}h"

    if row.linked_pr_url and not row.blockers and row.risk_level == "low":
        if row.linked_pr_draft:
            delay_hours = _hours_between(row.created_at, row.linked_pr_created_at)
            if delay_hours is not None and delay_hours <= 24:
                return "prioritize", f"draft PR linked in {int(round(delay_hours))}h"
            return "prioritize", "draft PR linked"
        return "prioritize", "linked PR active"

    if row.linked_pr_url:
        return "watch", "linked PR needs review"

    if row.risk_level != "low":
        return "watch", "risk signals need maintainer review"

    return "watch", "claim active; waiting for PR or update"


def _apply_risk_scores(
    results_by_issue: Dict[str, List[ClaimResult]],
    policy_name: str,
) -> None:
    flat_rows = [row for rows in results_by_issue.values() for row in rows]
    if not flat_rows:
        return

    inputs = [
        RiskClaimInput(
            claim_id=row.claim_id,
            user=row.user,
            issue_ref=row.issue_ref,
            created_at=row.created_at,
            body=row.body,
            account_age_days=row.account_age_days,
            claim_age_hours=row.claim_age_hours,
            silence_hours=row.silence_hours,
            wallet=row.wallet,
            proof_links=tuple(row.proof_links),
            linked_pr_url=row.linked_pr_url,
            linked_pr_state=row.linked_pr_state,
            linked_pr_draft=row.linked_pr_draft,
            linked_pr_created_at=row.linked_pr_created_at,
        )
        for row in flat_rows
    ]
    risk_by_claim = {
        item.claim_id: item
        for item in score_claims(inputs, policy_name=policy_name)
    }
    for rows in results_by_issue.values():
        for row in rows:
            risk = risk_by_claim.get(row.claim_id)
            if risk is None:
                continue
            row.risk_score = risk.score
            row.risk_level = risk.level
            row.risk_reasons = list(risk.reasons)
            row.action, row.action_reason = _derive_action(row)
        action_rank = {"prioritize": 0, "request_details": 1, "release_claim": 2, "watch": 3}
        rows.sort(
            key=lambda r: (
                action_rank.get(r.action, 9),
                -r.risk_score,
                r.status != "eligible",
                r.user.lower(),
            )
        )


def _format_pr_cell(row: ClaimResult) -> str:
    if not row.linked_pr_url or not row.linked_pr_ref:
        return ""
    if row.linked_pr_state == "open" and row.linked_pr_draft:
        state = "draft"
    elif row.linked_pr_state:
        state = row.linked_pr_state
    else:
        state = "linked"
    return f"[{state} {row.linked_pr_ref}]({row.linked_pr_url})"


def _format_payout_target(row: ClaimResult) -> str:
    parts = []
    if row.wallet:
        parts.append(f"`{row.wallet}`")
    if row.bottube_user:
        parts.append(f"`@{row.bottube_user}`")
    return " / ".join(parts)


def _build_report_md(
    generated_at: str,
    results_by_issue: Dict[str, List[ClaimResult]],
    since_hours: int,
    session_lookback_hours: int,
    risk_policy: str,
) -> str:
    lines: List[str] = []
    lines.append(f"### Auto-Triage Report ({generated_at})")
    lines.append(f"Window: last `{since_hours}`h")
    lines.append(f"Session lookback: `{session_lookback_hours}`h")
    lines.append(f"Risk policy: `{risk_policy}`")
    lines.append("")

    action_counts = {"prioritize": 0, "watch": 0, "request_details": 0, "release_claim": 0}
    for rows in results_by_issue.values():
        for row in rows:
            if row.action in action_counts:
                action_counts[row.action] += 1
    lines.append(
        "Maintainer actions: "
        + ", ".join(f"`{name}`={count}" for name, count in action_counts.items())
    )
    lines.append("")

    suspicious = sorted(
        [
            row
            for rows in results_by_issue.values()
            for row in rows
            if row.risk_level != "low"
        ],
        key=lambda row: (-row.risk_score, row.user.lower(), row.issue_ref.lower()),
    )
    lines.append("#### Suspicious Claims")
    if not suspicious:
        lines.append("_No medium/high risk claims in this window._")
    else:
        lines.append("| User | Issue | Action | Risk | Score | Reasons | PR | Comment |")
        lines.append("|---|---|---|---|---:|---|---|---|")
        for row in suspicious[:10]:
            reasons = ", ".join(row.risk_reasons)
            lines.append(
                f"| @{row.user} | {row.issue_ref} | `{row.action}` | `{row.risk_level}` | {row.risk_score} | {reasons} | {_format_pr_cell(row)} | [link]({row.comment_url}) |"
            )
    lines.append("")
    for issue_ref, rows in results_by_issue.items():
        lines.append(f"#### {issue_ref}")
        if not rows:
            lines.append("_No active claim sessions._")
            lines.append("")
            continue
        lines.append(
            "| User | Action | Risk | Score | Status | Acct(d) | Claim(h) | Idle(h) | PR | Payout | Reasons | Blockers | Comment |"
        )
        lines.append("|---|---|---|---:|---|---:|---:|---:|---|---|---|---|---|")
        for r in rows:
            age = "" if r.account_age_days is None else str(r.account_age_days)
            reasons = ", ".join(r.risk_reasons)
            blockers = ", ".join(r.blockers) if r.blockers else ""
            lines.append(
                f"| @{r.user} | `{r.action}` | `{r.risk_level}` | {r.risk_score} | `{r.status}` | {age} | {_format_hours(r.claim_age_hours)} | {_format_hours(r.silence_hours)} | {_format_pr_cell(r)} | {_format_payout_target(r)} | {reasons or r.action_reason} | {blockers} | [link]({r.comment_url}) |"
            )
        lines.append("")
    return "\n".join(lines).strip()


def _ignored_users() -> Set[str]:
    # Ignore maintainers/bots so their informational comments don't become
    # "claims" (which would pollute triage results).
    ignored = {"scottcjn", "github-actions[bot]", "sophiaeagent-beep"}
    extra = os.environ.get("TRIAGE_IGNORE_USERS", "").strip()
    if extra:
        for u in extra.split(","):
            u = u.strip().lower()
            if u:
                ignored.add(u)
    return ignored


def main() -> int:
    token = _env("GITHUB_TOKEN")
    since_hours = int(_env("SINCE_HOURS", "72"))
    session_lookback_hours = int(
        _env("TRIAGE_SESSION_LOOKBACK_HOURS", str(max(since_hours, 168)))
    )
    risk_policy = _env("TRIAGE_RISK_POLICY", "balanced")
    ignored_users = _ignored_users()
    targets_json = os.environ.get("TRIAGE_TARGETS_JSON", "").strip()
    if targets_json:
        targets = json.loads(targets_json)
    else:
        targets = DEFAULT_TARGETS

    # Build star cache only for repos we need.
    required_star_repos: Set[str] = set()
    for t in targets:
        for repo in t.get("required_stars", []):
            required_star_repos.add(repo)

    star_cache: Dict[str, Set[str]] = {}
    for repo in sorted(required_star_repos):
        users = _gh_paginated(f"/repos/Scottcjn/{repo}/stargazers", token)
        star_cache[repo] = {u.get("login") for u in users if u.get("login")}

    user_cache: Dict[str, Dict[str, Any]] = {}
    pr_cache: Dict[Tuple[str, str, int], Optional[LinkedPullRequest]] = {}
    now = _now_utc()
    session_cutoff = now - timedelta(hours=session_lookback_hours)

    results_by_issue: Dict[str, List[ClaimResult]] = {}
    for target in targets:
        owner = target["owner"]
        repo = target["repo"]
        issue = int(target["issue"])
        min_age = int(target.get("min_account_age_days", 0))
        req_wallet = bool(target.get("require_wallet", True))
        req_bt = bool(target.get("require_bottube_username", False))
        req_payout_target = bool(target.get("require_payout_target", False))
        req_proof = bool(target.get("require_proof_link", False))
        req_stars = list(target.get("required_stars", []))

        issue_ref = f"{owner}/{repo}#{issue}"
        issue_obj = _gh_request("GET", f"/repos/{owner}/{repo}/issues/{issue}", token)
        comments_url = issue_obj["comments_url"]
        comments = _gh_paginated(comments_url, token)
        sessions = _collect_claim_sessions(comments, owner, repo, issue_ref, ignored_users)
        timeline_refs = _load_timeline_pr_refs(owner, repo, issue, token)

        rows: List[ClaimResult] = []
        for session in sessions:
            user = session.user
            if user not in user_cache:
                try:
                    u = _gh_request("GET", f"/users/{user}", token)
                    created_at = u.get("created_at")
                    age_days = None
                    if created_at:
                        age_days = (now - _parse_iso(created_at)).days
                    user_cache[user] = {"age_days": age_days}
                except urllib.error.HTTPError:
                    user_cache[user] = {"age_days": None}

            age_days = user_cache[user]["age_days"]
            linked_prs = _linked_prs_for_session(session, timeline_refs, token, pr_cache)
            primary_pr = _select_primary_pr(linked_prs)
            last_activity_at = _session_last_activity(session, primary_pr)
            if (
                _parse_iso(session.created_at) < session_cutoff
                and _parse_iso(last_activity_at) < session_cutoff
            ):
                continue

            wallet = session.wallet
            bottube_user = session.bottube_user
            proof_links = sorted(session.proof_links)
            blockers: List[str] = []

            if age_days is not None and age_days < min_age:
                blockers.append(f"account_age<{min_age}")
            if req_payout_target:
                if not wallet and not bottube_user:
                    blockers.append("missing_payout_target")
            else:
                if req_wallet and not wallet:
                    blockers.append("missing_wallet")
            if wallet and _wallet_looks_external(wallet):
                blockers.append("wallet_external_format")
            if req_bt and not bottube_user:
                blockers.append("missing_bottube_username")
            if req_proof and not proof_links:
                blockers.append("missing_proof_link")

            for star_repo in req_stars:
                if user not in star_cache.get(star_repo, set()):
                    blockers.append(f"missing_star:{star_repo}")

            rows.append(
                ClaimResult(
                    claim_id=session.first_claim_url or f"{issue_ref}:{user}:{session.created_at}",
                    user=user,
                    issue_ref=issue_ref,
                    comment_url=session.latest_update_url or session.first_claim_url,
                    created_at=session.created_at,
                    account_age_days=age_days,
                    wallet=wallet,
                    bottube_user=bottube_user,
                    blockers=blockers,
                    proof_links=proof_links,
                    body=session.first_claim_body,
                    latest_update_at=session.latest_update_at,
                    claim_age_hours=_hours_since(session.created_at, now),
                    silence_hours=_hours_since(last_activity_at, now),
                    linked_pr_ref=primary_pr.short_ref if primary_pr else None,
                    linked_pr_url=primary_pr.url if primary_pr else None,
                    linked_pr_state=primary_pr.state if primary_pr else None,
                    linked_pr_draft=primary_pr.draft if primary_pr else None,
                    linked_pr_created_at=primary_pr.created_at if primary_pr else None,
                    linked_pr_updated_at=primary_pr.updated_at if primary_pr else None,
                )
            )

        results_by_issue[issue_ref] = rows

    _apply_risk_scores(results_by_issue, risk_policy)

    generated_at = now.isoformat().replace("+00:00", "Z")
    report = _build_report_md(
        generated_at,
        results_by_issue,
        since_hours,
        session_lookback_hours,
        risk_policy,
    )
    print(report)

    ledger_repo = os.environ.get("LEDGER_REPO", "").strip()
    ledger_issue = os.environ.get("LEDGER_ISSUE", "").strip()
    if ledger_repo and ledger_issue:
        issue_path = f"/repos/Scottcjn/{ledger_repo}/issues/{int(ledger_issue)}"
        ledger = _gh_request("GET", issue_path, token)
        body = ledger.get("body") or ""
        new_block = f"{MARKER_START}\n{report}\n{MARKER_END}"
        if MARKER_START in body and MARKER_END in body:
            start = body.index(MARKER_START)
            end = body.index(MARKER_END) + len(MARKER_END)
            updated = f"{body[:start]}{new_block}{body[end:]}"
        else:
            updated = f"{body}\n\n{new_block}\n"
        _gh_request("PATCH", issue_path, token, data={"body": updated})
        print(f"\nUpdated ledger issue: Scottcjn/{ledger_repo}#{ledger_issue}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - runtime safety for actions logs
        print(f"auto-triage failed: {exc}", file=sys.stderr)
        raise
