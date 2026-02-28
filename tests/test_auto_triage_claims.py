import unittest

from scripts.auto_triage_claims import (
    ClaimResult,
    _apply_risk_scores,
    _build_report_md,
    _collect_claim_sessions,
    _derive_action,
    _extract_bottube_user,
    _extract_wallet,
    _has_proof_link,
    _looks_like_claim,
)


class AutoTriageClaimsTests(unittest.TestCase):
    def test_extract_wallet_supports_miner_id_space(self):
        body = "Claim\nMiner id: abc_123_wallet\nProof: https://example.com/proof"
        self.assertEqual(_extract_wallet(body), "abc_123_wallet")

    def test_extract_wallet_supports_miner_id_hyphen(self):
        body = "Payout target miner-id: zk_worker_007"
        self.assertEqual(_extract_wallet(body), "zk_worker_007")

    def test_extract_wallet_supports_chinese_label(self):
        body = "钱包地址： zh_wallet_01"
        self.assertEqual(_extract_wallet(body), "zh_wallet_01")

    def test_extract_bottube_user_from_profile_link(self):
        body = "BoTTube profile: https://bottube.ai/@energypantry"
        self.assertEqual(_extract_bottube_user(body), "energypantry")

    def test_has_proof_link(self):
        self.assertTrue(_has_proof_link("Demo: https://github.com/foo/bar/pull/1"))
        self.assertFalse(_has_proof_link("No links included"))

    def test_looks_like_claim(self):
        self.assertTrue(_looks_like_claim("Claiming this bounty. Wallet: abc_123"))
        self.assertFalse(_looks_like_claim("General discussion about roadmap and release timing."))

    def test_collect_claim_sessions_keeps_first_claim_and_latest_update(self):
        comments = [
            {
                "user": {"login": "builder"},
                "created_at": "2026-02-27T23:00:00Z",
                "html_url": "https://example.com/c-0",
                "body": "General roadmap discussion.",
            },
            {
                "user": {"login": "builder"},
                "created_at": "2026-02-28T00:00:00Z",
                "html_url": "https://example.com/c-1",
                "body": "Claiming this bounty. Wallet: rtc_builder_01",
            },
            {
                "user": {"login": "builder"},
                "created_at": "2026-02-28T12:00:00Z",
                "html_url": "https://example.com/c-2",
                "body": "Draft PR #55 is up with tests attached.",
            },
        ]

        sessions = _collect_claim_sessions(
            comments,
            "Scottcjn",
            "rustchain-bounties",
            "Scottcjn/rustchain-bounties#476",
            set(),
        )

        self.assertEqual(len(sessions), 1)
        session = sessions[0]
        self.assertEqual(session.first_claim_url, "https://example.com/c-1")
        self.assertEqual(session.latest_update_url, "https://example.com/c-2")
        self.assertEqual(session.wallet, "rtc_builder_01")
        self.assertIn(("Scottcjn", "rustchain-bounties", 55), session.pr_refs)

    def test_derive_action_prioritizes_fast_draft_pr(self):
        row = ClaimResult(
            claim_id="c-1",
            user="builder",
            issue_ref="Scottcjn/rustchain-bounties#476",
            comment_url="https://example.com/c-2",
            created_at="2026-02-28T00:00:00Z",
            account_age_days=400,
            wallet="rtc_builder_01",
            bottube_user=None,
            blockers=[],
            claim_age_hours=6,
            silence_hours=1,
            linked_pr_ref="Scottcjn/rustchain-bounties#495",
            linked_pr_url="https://github.com/Scottcjn/rustchain-bounties/pull/495",
            linked_pr_state="open",
            linked_pr_draft=True,
            linked_pr_created_at="2026-02-28T03:00:00Z",
            risk_level="low",
        )
        action, reason = _derive_action(row)
        self.assertEqual(action, "prioritize")
        self.assertIn("draft PR linked", reason)

    def test_derive_action_releases_stale_claim_without_pr(self):
        row = ClaimResult(
            claim_id="c-1",
            user="builder",
            issue_ref="Scottcjn/rustchain-bounties#476",
            comment_url="https://example.com/c-1",
            created_at="2026-02-28T00:00:00Z",
            account_age_days=400,
            wallet="rtc_builder_01",
            bottube_user=None,
            blockers=[],
            claim_age_hours=30,
            silence_hours=30,
            risk_level="low",
        )
        action, reason = _derive_action(row)
        self.assertEqual(action, "release_claim")
        self.assertIn("no linked PR", reason)

    def test_build_report_includes_suspicious_claims_summary(self):
        results_by_issue = {
            "Scottcjn/rustchain-bounties#476": [
                ClaimResult(
                    claim_id="c-1",
                    user="fresh-bot",
                    issue_ref="Scottcjn/rustchain-bounties#476",
                    comment_url="https://example.com/c-1",
                    created_at="2026-02-28T00:00:00Z",
                    account_age_days=1,
                    wallet="shared_wallet",
                    bottube_user=None,
                    blockers=[],
                    proof_links=["https://example.com/proof"],
                    body="Claiming this bounty. Wallet: shared_wallet. Proof: https://example.com/proof",
                    claim_age_hours=30,
                    silence_hours=30,
                ),
                ClaimResult(
                    claim_id="c-2",
                    user="steady-user",
                    issue_ref="Scottcjn/rustchain-bounties#476",
                    comment_url="https://example.com/c-2",
                    created_at="2026-02-28T01:00:00Z",
                    account_age_days=400,
                    wallet="steady_wallet",
                    bottube_user=None,
                    blockers=[],
                    proof_links=["https://example.com/unique-proof"],
                    body="Claiming this bounty with a unique implementation plan.",
                    claim_age_hours=3,
                    silence_hours=1,
                    linked_pr_ref="Scottcjn/rustchain-bounties#500",
                    linked_pr_url="https://github.com/Scottcjn/rustchain-bounties/pull/500",
                    linked_pr_state="open",
                ),
            ]
        }

        _apply_risk_scores(results_by_issue, "balanced")
        report = _build_report_md(
            "2026-02-28T02:00:00Z",
            results_by_issue,
            72,
            168,
            "balanced",
        )

        self.assertIn("#### Suspicious Claims", report)
        self.assertIn("@fresh-bot", report)
        self.assertIn("ACCOUNT_AGE", report)
        self.assertIn("Maintainer actions", report)
        self.assertIn("`release_claim`", report)


if __name__ == "__main__":
    unittest.main()
